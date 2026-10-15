"""File ingestion and parsing logic for BOMs and Inventory.

This module handles the extraction of component data from various file formats
(PDF, CSV, Text). It orchestrates the line-by-line reading, cleaning, and
delegation to the classification engine.
"""

import csv
import logging
import re
import traceback

from src.bom_lib import constants
from src.bom_lib.builds import BuildPlan, Entry, PageContext, plan_builds
from src.bom_lib.classifier import categorize_part, normalize_value_by_category
from src.bom_lib.designators import parse_designator
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.layout import find_listings, page_lines
from src.bom_lib.types import (
    BuildVariant,
    Inventory,
    OptionNote,
    ParseResult,
    PDFPageExtraction,
    PDFWord,
    StatsDict,
    create_empty_inventory,
    create_empty_stats,
    make_component_key,
)
from src.bom_lib.utils import expand_refs
from src.bom_lib.verification import (
    check_bom_reconciliation,
    check_classification,
    check_duplicate,
    check_unextracted_refs,
)

# Characters further apart than this start a new word. pdfplumber's 3pt default
# merges words in tightly kerned documents (Muffin Fuzz draws word spaces of
# 1.3pt between overlapping glyphs: 'TONEBYPASSSWITCH').
_WORD_GAP_PT = 1.0

# Initialize Logger
logger = logging.getLogger(__name__)


def ingest_bom_line(
    inventory: Inventory,
    source: str,
    ref_raw: str,
    val_raw: str,
    stats: StatsDict | None = None,
    category_hint: ComponentCategory | None = None,
) -> int:
    """Core ingestion kernel: Expands refs, classifies, and updates inventory.

    This function bridges the gap between raw text (R1-R4, 10k) and the
    structured inventory. It handles:
    1. Reference expansion (R1-R4 -> R1, R2, R3, R4).
    2. De-duplication (checking if R1 was already seen in this session).
    3. Categorization (identifying parts).
    4. Auto-injection (adding sockets for ICs).

    Args:
        inventory: The master inventory dictionary to update.
        source: The name of the file/project being ingested.
        ref_raw: Raw reference string (e.g., "R1-R4").
        val_raw: Raw value string (e.g., "10k").
        stats: Optional stats object to track duplicate refs and counts.
        category_hint: Category from a TYPE column, for named parts the
            designator and value cannot classify.

    Returns:
        The number of valid parts successfully found and recorded.
    """
    parts_found = 0
    expanded_refs = expand_refs(ref_raw)

    for r in expanded_refs:
        result = categorize_part(r, val_raw, category_hint)
        if result is None:
            # Not a part (e.g. a jumper pad setting), so it cannot conflict
            # with anything either.
            continue

        # De-dupe Check
        if stats is not None and "seen_refs" in stats:
            if r in stats["seen_refs"]:
                check_duplicate(stats, r, stats["seen_refs"][r], val_raw)
                continue
            stats["seen_refs"][r] = val_raw

        if stats is not None:
            check_classification(stats, r, val_raw, result.category, result.clean_value)

        parts_found += 1
        main_key = make_component_key(result.category, result.clean_value)

        # 1. Record Main Part
        inventory.add_part(source, main_key, r)

        # 2. Handle Auto-Injection (e.g., Sockets)
        if result.injected_key:
            # Record auto-injected component key (e.g. IC socket)
            inventory.add_part(source, result.injected_key, f"{r} (Inj)")

    return parts_found


def _record_pcb(
    inventory: Inventory, source_name: str, name: str, stats: StatsDict
) -> None:
    key = make_component_key(ComponentCategory.PCB, name)
    inventory[key]["qty"] += 1
    if "PCB" not in inventory[key]["sources"][source_name]:
        inventory[key]["sources"][source_name].append("PCB")
    stats["parts_found"] += 1


def parse_with_verification(
    bom_list: list[str], source_name: str = "Manual Input"
) -> ParseResult:
    """Parses a list of raw text strings (Manual BOM Input).

    Handles standard formats like "R1 10k" and special cases like "PCB Name".

    Args:
        bom_list: List of strings (lines from a text area).
        source_name: Label for the source of these parts.

    Returns:
        A ParseResult containing updated inventory and parsing statistics.
    """
    inventory = create_empty_inventory()
    stats: StatsDict = create_empty_stats()

    # Regex: Matches Ref + Separator + Value.
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)[\s,]+(\S.*?)\s*$")

    for raw_text in bom_list:
        lines = raw_text.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue
            stats["lines_read"] += 1

            # Check for PCB Definition (Prefix "PCB" + Value)
            parts = line.split(None, 1)
            if parts and parts[0].upper() == "PCB":
                # Skip header "PCB"
                if len(parts) == 1:
                    continue

                clean_name = parts[1].strip()
                _record_pcb(inventory, source_name, clean_name, stats)
                continue

            match = pattern.match(line)
            success = False

            if match:
                ref_raw = match.group(1).upper()
                val_raw = match.group(2)
                count = ingest_bom_line(inventory, source_name, ref_raw, val_raw, stats)
                if count > 0:
                    stats["parts_found"] += count
                    success = True

            if not success:
                # Fallback: Check if line contains "PCB" (heuristic)
                if "PCB" in line.upper() and line.strip().upper() != "PCB":
                    _record_pcb(inventory, source_name, line.strip(), stats)
                else:
                    stats["residuals"].append(line)

    return ParseResult(inventory=inventory, stats=stats)


def parse_csv_bom(filepath: str, source_name: str) -> ParseResult:
    """Parses a CSV BOM file.

    Attempts to intelligently guess columns ('Ref', 'Value') or falls back
    to using the first two columns.

    Args:
        filepath: Path to the CSV file.
        source_name: Label for the source.

    Returns:
        A ParseResult containing updated inventory and parsing statistics.
    """
    inventory = create_empty_inventory()
    stats: StatsDict = create_empty_stats()

    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["lines_read"] += 1
            row_clean = {str(k).lower().strip(): v for k, v in row.items() if k}

            # Try explicit columns
            ref = (
                row_clean.get("ref")
                or row_clean.get("designator")
                or row_clean.get("part")
                or row_clean.get("location")
            )
            val = (
                row_clean.get("value")
                or row_clean.get("val")
                or row_clean.get("description")
            )

            # Fallback: Assume Col 1 = Ref, Col 2 = Val
            if not ref and not val and len(row_clean) == 2:
                vals = list(row_clean.values())
                ref = vals[0]
                val = vals[1]

            success = False
            if ref and val:
                count = ingest_bom_line(inventory, source_name, ref, val, stats)
                if count > 0:
                    stats["parts_found"] += count
                    success = True

            if not success:
                stats["residuals"].append(str(row))

    return ParseResult(inventory=inventory, stats=stats)


def parse_user_inventory(filepath: str) -> Inventory:
    """Parses a user's stock CSV.

    Expects columns: Category, Part, Qty.
    Applies the same value normalization as BOM parsing to ensure keys match.

    Args:
        filepath: Path to the user inventory CSV.

    Returns:
        A populated Inventory dictionary.
    """
    stock: Inventory = create_empty_inventory()

    with open(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_clean = {k.lower(): v for k, v in row.items() if k}

            cat = row_clean.get("category", "").strip()
            val = row_clean.get("part", "").strip()
            qty_str = row_clean.get("qty", "0").strip()

            if cat and val:
                try:
                    qty = int(qty_str)
                except ValueError:
                    continue

                # Critical: Normalize value so it matches BOM keys

                try:
                    cat_enum = ComponentCategory(cat)
                except ValueError:
                    continue
                clean_val = normalize_value_by_category(cat_enum, val)
                key = make_component_key(cat_enum, clean_val)

                stock.add_part("User Stock", key, ref="", qty=qty)

    return stock


_PART_NUMBER = re.compile(r"\b\d?[A-Z]{1,4}\d{2,5}[A-Z]{0,2}\b")
_POLE_THROW = re.compile(r"\b(?:[SD34]P[DS]T|\dP\dT)\b")


_TYPE_HINTS: tuple[tuple[str, ComponentCategory], ...] = (
    ("footswitch", ComponentCategory.SWITCHES),
    ("jack", ComponentCategory.HARDWARE_MISC),
    ("relay", ComponentCategory.HARDWARE_MISC),
    ("transformer", ComponentCategory.HARDWARE_MISC),
    ("inductor", ComponentCategory.HARDWARE_MISC),
    ("fuse", ComponentCategory.HARDWARE_MISC),
    ("switch", ComponentCategory.SWITCHES),
    ("trimmer", ComponentCategory.POTENTIOMETERS),
    ("pot", ComponentCategory.POTENTIOMETERS),
    ("led", ComponentCategory.DIODES),
    ("diode", ComponentCategory.DIODES),
    ("transistor", ComponentCategory.TRANSISTORS),
    ("mosfet", ComponentCategory.TRANSISTORS),
)
_LED_DESCRIPTION = re.compile(r"[^,]*\bLED\b", re.IGNORECASE)
# Not preceded by "non-": a "Non-latching relay" is not latching.
_ACTUATION = re.compile(r"(?<!non-)(?<!non )\b(momentary|latching)\b", re.IGNORECASE)
_DUAL_GANG = re.compile(r"\bDUAL[- ]?GANG")
_POSITIONS = re.compile(r"\b(?:ON|OFF)(?:/(?:ON|OFF)){1,2}\b")
# Capacitor constructions a TYPE cell may name that the VALUE cell omits.
_DIELECTRICS = (("TANTALUM", "Tantalum"), ("MLCC", "MLCC"))


_VOLTAGE = re.compile(r"\b(\d+(?:\.\d+)?)\s*V\b", re.IGNORECASE)


def rating_from_notes(ref: str, value: str, notes: str) -> str:
    """Appends a capacitor voltage rating that only appears in the NOTES column.

    Taqueria lists C102 as '10u' with the note '25V Minimum Voltage'; the
    rating is part of what must be bought.

    Args:
        ref: The row's designator.
        value: The (possibly enriched) VALUE cell.
        notes: The NOTES cell of the same row.

    Returns:
        The value with '(<rating> minimum)' appended for capacitors whose notes
        give a voltage the value does not already state.
    """
    designator = parse_designator(ref)
    if designator is None or designator.category != ComponentCategory.CAPACITORS:
        return value
    rating = _VOLTAGE.search(notes)
    if rating is None or _VOLTAGE.search(value):
        return value
    return f"{value} ({rating.group(1)}V minimum)"


def category_from_type(type_text: str) -> ComponentCategory | None:
    """Category suggested by a TYPE column description ('Stereo Jack' -> Hardware)."""
    low = type_text.lower()
    return next((cat for word, cat in _TYPE_HINTS if word in low), None)


def enrich_value_from_type(value: str, type_text: str, notes: str = "") -> str:
    """Fills in a generic VALUE cell from the row's TYPE and NOTES descriptions.

    Some parts lists put a shorthand in VALUE and the purchasable detail in
    TYPE (Kliche: 'GE' / 'Germanium diode, 1N34A'; Gravitation: 'ON/ON' /
    'Toggle switch, SPDT 2-position (ON/ON)') or in NOTES (Liquid Crystal:
    'DPDT' / 'Toggle switch, DPDT' / '2-position (ON/ON)').

    Args:
        value: The VALUE cell.
        type_text: The TYPE cell of the same row.
        notes: The NOTES cell of the same row, if the table has one.

    Returns:
        The value, completed with a part number, LED description, pole/throw
        code, switch positions, momentary/latching actuation, dual-gang pot,
        or capacitor construction (tantalum, MLCC) when the VALUE cell lacks one and the
        TYPE or NOTES cell provides it.
    """
    type_text = " ".join(type_text.split())
    detail = f"{type_text} {' '.join(notes.split())}".upper()
    if not any(ch.isdigit() for ch in value):
        part = _PART_NUMBER.search(type_text)
        if part:
            return part.group(0)
        # 'RED' / '3mm Red LED, Diffused'; 'LED' / 'Indicator LED'.
        led = _LED_DESCRIPTION.match(type_text)
        if led:
            return led.group(0).strip()
    actuation = _ACTUATION.search(type_text)
    if actuation and actuation.group(1).lower() not in value.lower():
        value = f"{value} {actuation.group(1).capitalize()}"
    if constants.SWITCH_VALUE_PATTERN.search(value.upper()) and not _POLE_THROW.search(
        value.upper()
    ):
        pole = _POLE_THROW.search(type_text.upper())
        if pole:
            return f"{pole.group(0)} {value}"
    if _POLE_THROW.fullmatch(value.upper()):
        positions = _POSITIONS.search(detail)
        if positions:
            return f"{value} {positions.group(0)}"
    if _DUAL_GANG.search(detail) and "DUAL" not in value.upper():
        return f"{value} Dual"
    if "CAPACITOR" in detail and any(ch.isdigit() for ch in value):
        for keyword, name in _DIELECTRICS:
            if keyword in detail and keyword not in value.upper():
                return f"{value} {name}"
    return value


def _table_entries(
    pages_data: list[PDFPageExtraction], stats: StatsDict
) -> list[Entry]:
    """Strategy 1: Extract entries from ruled tables (LOCATION / VALUE / TYPE / NOTES).

    Args:
        pages_data: Pre-extracted page data.
        stats: The statistics dictionary for tracking progress.

    Returns:
        One entry per table row that names a designator and a value.
    """
    entries: list[Entry] = []
    table_no = -1
    for page_no, data in enumerate(pages_data):
        for table in data["tables"]:
            if not table:
                continue
            table_no += 1

            # Header mapping
            headers = [str(h).upper().strip() for h in table[0] if h]
            loc_idx = -1
            val_idx = -1
            type_idx = -1
            notes_idx = -1
            start_row_idx = 1

            for i, h in enumerate(headers):
                if h in ("LOCATION", "REF", "DESIGNATOR", "PART"):
                    loc_idx = i
                elif h in ("VALUE", "VAL", "DESCRIPTION"):
                    val_idx = i
                elif h == "TYPE":
                    type_idx = i
                elif h == "NOTES":
                    notes_idx = i

            # Fallback mapping
            if loc_idx == -1 or val_idx == -1:
                loc_idx, val_idx, start_row_idx = 0, 1, 0

            follows_or = False
            for row in table[start_row_idx:]:
                stats["lines_read"] += 1
                row_safe = [str(cell) if cell else "" for cell in row]
                filled = [c.strip() for c in row_safe if c.strip()]
                if filled and all(c.upper() == "OR" for c in filled):
                    # Shindig: an 'OR' row between two alternatives for one spot.
                    follows_or = True
                    continue

                # Skip Summary lines (e.g., "1 x 100k")
                first_content = next((c for c in row_safe if c), "")
                if re.match(r"^\d+\s*[xX]", first_content):
                    continue

                ref_raw = ""
                val_raw = ""
                hint: ComponentCategory | None = None

                if len(row_safe) > max(loc_idx, val_idx):
                    ref_raw = row_safe[loc_idx].replace("\n", " ").strip()
                    val_raw = " ".join(row_safe[val_idx].split())
                    # Optional-part markers are not part of the value ('*27K*'),
                    # but a value of only markers is a placeholder and stays one.
                    unmarked = " ".join(val_raw.replace("*", " ").split())
                    if unmarked:
                        val_raw = unmarked
                    if 0 <= type_idx < len(row_safe):
                        type_text = " ".join(row_safe[type_idx].split())
                        # Kickstart: 'SW1 | (blank) | 6mm x 6mm Push Button Switch'.
                        if not val_raw and type_text:
                            val_raw = type_text
                        notes = (
                            row_safe[notes_idx]
                            if 0 <= notes_idx < len(row_safe)
                            else ""
                        )
                        val_raw = enrich_value_from_type(
                            val_raw, row_safe[type_idx], notes
                        )
                        hint = category_from_type(row_safe[type_idx])
                    if 0 <= notes_idx < len(row_safe):
                        val_raw = rating_from_notes(
                            ref_raw, val_raw, row_safe[notes_idx]
                        )
                elif len(row_safe) == 1 and row_safe[0]:
                    parts = row_safe[0].strip().split(None, 1)
                    if len(parts) == 2:
                        ref_raw, val_raw = parts[0].strip(), parts[1].strip()

                if ref_raw and val_raw:
                    entries.append(
                        Entry(
                            ref_raw,
                            val_raw,
                            page_no,
                            table=table_no,
                            hint=hint,
                            follows_or=follows_or,
                        )
                    )
                    follows_or = False
    return entries


def _is_not_populated(value: str) -> bool:
    return value.upper() in constants.NOT_POPULATED_VALUES


def _record_not_populated(stats: StatsDict, ref: str, value: str) -> None:
    """Marks a deliberately unpopulated designator as seen.

    It is not a part, but if the document lists the same designator again with
    a real value (another variant's parts list), that conflict must be flagged
    rather than silently merged into this build.
    """
    for r in expand_refs(ref):
        stats["seen_refs"].setdefault(r, value)


def _is_ignored(value: str) -> bool:
    words = set(re.findall(r"[A-Z]+", value.upper()))
    phrase = " ".join(value.upper().split())
    return any(
        bad in words if " " not in bad else bad in phrase
        for bad in constants.IGNORE_VALUES
    )


def _layout_entries(
    pages_data: list[PDFPageExtraction], stats: StatsDict, table_refs: set[str]
) -> list[Entry]:
    """Strategy 2: Extract entries from the page layout.

    Reads word positions to pair each designator with the value printed in the
    neighboring column (see `layout.py`). Designators the table strategy already
    found are skipped, so the two strategies never disagree about a part.

    Args:
        pages_data: Pre-extracted page data including word positions.
        stats: The statistics dictionary for tracking progress.
        table_refs: Designators already extracted from ruled tables.

    Returns:
        One entry per listing, with its page position.
    """
    entries: list[Entry] = []
    for page_no, data in enumerate(pages_data):
        for listing in find_listings(data["words"]):
            ref, value = listing.ref, listing.value
            stats["lines_read"] += 1

            if ref in table_refs:
                continue
            entry = Entry(ref, value, page_no, line=listing.line, x0=listing.x0)
            if not entry.not_populated and (_is_ignored(value) or len(value) > 50):
                continue
            if ref in constants.KEYWORDS and not entry.not_populated:
                # Named controls must carry a real value (B100K, SPDT ...).
                has_digit = any(ch.isdigit() for ch in value)
                if not has_digit and not constants.SWITCH_VALUE_PATTERN.search(
                    value.upper()
                ):
                    continue
            entries.append(entry)
    return entries


def _ingest_entries(
    entries: list[Entry], source_name: str
) -> tuple[Inventory, StatsDict]:
    """Ingests one build's entries into a fresh inventory.

    Each build gets its own duplicate tracking, so two variants listing the
    same designator are not conflicts, while a conflict inside one build is.
    """
    inventory = create_empty_inventory()
    stats = create_empty_stats()
    for entry in entries:
        if entry.not_populated:
            _record_not_populated(stats, entry.ref, entry.value)
            continue
        stats["parts_found"] += ingest_bom_line(
            inventory, source_name, entry.ref, entry.value, stats, entry.hint
        )
    return inventory, stats


def _merge_warnings(target: StatsDict, source: StatsDict) -> None:
    for warning in source["warnings"]:
        if warning not in target["warnings"]:
            target["warnings"].append(warning)


def _assemble_builds(
    plan: BuildPlan, source_name: str, stats: StatsDict
) -> tuple[Inventory, tuple[BuildVariant, ...], tuple[OptionNote, ...], Inventory]:
    """Ingests every build in a plan.

    Returns:
        The single-build inventory (empty when the document has variants), the
        variants, the option notes, and the union of every build and option
        choice (for the self-checks, which must see everything the document lists).
    """
    options = tuple(
        OptionNote(
            group=o.group,
            chosen=o.choices[0][0],
            alternatives=tuple(name for name, _ in o.choices[1:]),
        )
        for o in plan.options
    )
    # Merge each build and option choice separately: one variant's OMIT must not
    # hide another variant's part for the same designator.
    union = create_empty_inventory()
    builds = [plan.build_entries(v) for v in plan.variants] or [plan.build_entries()]
    builds += [list(choice) for o in plan.options for _, choice in o.choices[1:]]
    for entries in builds:
        union.merge(_ingest_entries(entries, source_name)[0])

    if len(plan.variants) < 2:
        variant = plan.variants[0] if plan.variants else None
        inventory, build_stats = _ingest_entries(
            plan.build_entries(variant), source_name
        )
        _merge_warnings(stats, build_stats)
        stats["parts_found"] += build_stats["parts_found"]
        return inventory, (), options, union

    variants = []
    for planned in plan.variants:
        inventory, build_stats = _ingest_entries(
            plan.build_entries(planned), source_name
        )
        _merge_warnings(stats, build_stats)
        variants.append(
            BuildVariant(planned.name, inventory, build_stats["parts_found"])
        )
    return create_empty_inventory(), tuple(variants), options, union


def parse_pedalpcb_pdf(filepath: str, source_name: str) -> ParseResult:
    """Parses a PedalPCB Build Document (PDF).

    Uses a multi-stage strategy:
    1. Visual Table Extraction (via pdfplumber lines).
    2. Layout analysis of word positions (column-aware text fallback).
    3. Build planning: variants (the builder must choose) and options (the
       document's first choice is used and the alternative noted).

    Args:
        filepath: Path to the PDF file.
        source_name: Label for the source.

    Returns:
        A ParseResult containing updated inventory, parsing statistics, and extracted title.
    """
    # Lazy import to avoid loading heavy PDF libraries unless needed
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed.")
        err_stats = create_empty_stats()
        err_stats["errors"].append("Missing dependency: pdfplumber")
        return ParseResult(inventory=create_empty_inventory(), stats=err_stats)

    inventory = create_empty_inventory()
    stats: StatsDict = create_empty_stats()
    variants: tuple[BuildVariant, ...] = ()
    options: tuple[OptionNote, ...] = ()

    pdf = None
    try:
        # Phase 1: File Access
        # We isolate this to distinguish between "Bad File" and "Bad Code"
        try:
            pdf = pdfplumber.open(filepath)
        except Exception as e:
            logger.error(f"Failed to open PDF {source_name}: {e}")
            stats["errors"].append(f"File Error: {e!s}")
            return ParseResult(inventory=inventory, stats=stats)

        # Phase 2: Extraction
        try:
            # Iterate pages once to grab expensive text/table data
            pages_data: list[PDFPageExtraction] = []
            for page in pdf.pages:
                pages_data.append(
                    {
                        # Same word gap as the layout reader: kerned cells
                        # otherwise run together ('3mmRedLED,Diffused').
                        "tables": page.extract_tables(
                            {"text_x_tolerance": _WORD_GAP_PT}
                        ),
                        "text": page.extract_text(),
                        "words": [
                            PDFWord(
                                text=w["text"],
                                x0=w["x0"],
                                x1=w["x1"],
                                top=w["top"],
                                size=w["size"],
                            )
                            for w in page.extract_words(
                                x_tolerance=_WORD_GAP_PT, extra_attrs=["size"]
                            )
                        ],
                    }
                )

            # --- PHASE 2: TITLE EXTRACTION ---
            try:
                p1 = pdf.pages[0]
                words = p1.extract_words(extra_attrs=["size"])
                ignore = {
                    "PEDALPCB",
                    "CONTROLS",
                    "COPYRIGHT",
                    "WWW.PEDALPCB.COM",
                    "LEVEL",
                    "VCC",
                    "GND",
                }
                candidates = [
                    w
                    for w in words
                    if w["text"].upper() not in ignore and w["size"] > 10
                ]

                if candidates:
                    max_size = max(c["size"] for c in candidates)
                    title_words = [w for w in candidates if w["size"] >= max_size - 1]
                    title_words.sort(key=lambda x: (x["top"], x["x0"]))
                    stats["extracted_title"] = " ".join(w["text"] for w in title_words)
            except Exception:
                # Title extraction is heuristic/optional; don't fail the build if it misses.
                pass

            # --- STRATEGY 1: TABLE EXTRACTION ---
            entries = _table_entries(pages_data, stats)

            # --- STRATEGY 2: LAYOUT ANALYSIS ---
            # Recovers anything the tables missed (or everything, for column layouts)
            entries += _layout_entries(pages_data, stats, {e.ref for e in entries})

            # --- BUILDS: variants and options ---
            pages = [
                PageContext(page_lines(data["words"]), data["text"] or "")
                for data in pages_data
            ]
            plan = plan_builds(entries, pages)
            inventory, variants, options, union = _assemble_builds(
                plan, source_name, stats
            )

            # --- SELF-CHECK ---
            # Every designator listed in the document must be accounted for, in
            # some build or option.
            page_texts = [data["text"] or "" for data in pages_data]
            check_unextracted_refs(page_texts, union, stats)
            check_bom_reconciliation(page_texts, union, stats)

        except Exception as e:
            # Capture internal parsing errors (logic bugs, layout changes)
            logger.error(f"Parsing logic failed for {source_name}")
            logger.error(traceback.format_exc())
            stats["errors"].append(f"Parse Error: {e!s}")

    finally:
        # Always close the file handle
        if pdf:
            pdf.close()

    return ParseResult(
        inventory=inventory,
        stats=stats,
        title=stats.get("extracted_title"),
        variants=variants,
        options=options,
    )
