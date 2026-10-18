"""File ingestion and parsing logic for BOMs and Inventory.

This module handles the extraction of component data from various file formats
(PDF, CSV, Text). It orchestrates the line-by-line reading, cleaning, and
delegation to the classification engine.
"""

import csv
import logging
import re
import traceback
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.bom_lib import constants

if TYPE_CHECKING:
    from pdfplumber.page import Page
from src.bom_lib.builds import BuildPlan, Entry, PageContext, plan_builds
from src.bom_lib.classifier import (
    DIP_SWITCH,
    categorize_part,
    normalize_value_by_category,
)
from src.bom_lib.designators import is_designator, parse_designator
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.layout import (
    REF_RANGE,
    Cell,
    Listing,
    bom_note_cells,
    clean_value,
    find_bom_rows,
    find_listings,
    footnote_marks,
    page_lines,
    strip_asides,
)
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
from src.bom_lib.utils import expand_refs, parse_value_to_decimal
from src.bom_lib.verification import (
    check_bom_reconciliation,
    check_classification,
    check_duplicate,
    check_footnotes,
    check_marking_code,
    check_parts_found,
    check_row_quantity,
    check_second_board,
    check_unclassified_row,
    check_unextracted_refs,
    check_version_note,
    resolve_value_choice,
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
    qty: int = 1,
    inject: bool = True,
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
        qty: How many of each designator to buy ('TERMINAL BLOCK (Qty 3)').
        inject: Whether to add implied parts (IC sockets); off when the
            document lists its own sockets (Aion FX: 'IC1-S | DIP-8 socket').

    Returns:
        The number of valid parts successfully found and recorded.
    """
    parts_found = 0
    expanded_refs = expand_refs(ref_raw)
    val_raw = resolve_value_choice(stats, ref_raw, val_raw)

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
        inventory.add_part(source, main_key, r, qty)

        # 2. Handle Auto-Injection (e.g., Sockets)
        if result.injected_key and inject:
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


_REF_QUANTITY = re.compile(r"\s*\(\s*qty\.?\s*(\d+)\s*\)\s*$", re.IGNORECASE)
_PART_NUMBER = re.compile(r"\b\d?[A-Z]{1,4}\d{2,5}[A-Z]{0,2}\b")
_POLE_THROW = re.compile(r"\b(?:[SD34]P[DS]T|\dP\dT)\b")
# The first of a list of interchangeable parts: '(7530, 7532, 9203, or similar)'.
_NOTES_CHOICE = re.compile(r"\(?\s*([A-Z]*\d[\w./-]*)\s*,")


_TYPE_HINTS: tuple[tuple[str, ComponentCategory], ...] = (
    ("footswitch", ComponentCategory.SWITCHES),
    # Aion FX names every row's construction: 'Metal film resistor, 1/4W',
    # 'IC socket, DIP-8', 'Enclosure, die-cast aluminum', '9V battery snap'.
    ("socket", ComponentCategory.HARDWARE_MISC),
    ("enclosure", ComponentCategory.HARDWARE_MISC),
    ("battery", ComponentCategory.HARDWARE_MISC),
    ("dc-dc converter", ComponentCategory.HARDWARE_MISC),
    ("photocoupler", ComponentCategory.OPTOELECTRONICS),
    ("vactrol", ComponentCategory.OPTOELECTRONICS),
    ("optocoupler", ComponentCategory.OPTOELECTRONICS),
    ("ldr", ComponentCategory.OPTOELECTRONICS),
    ("jack", ComponentCategory.HARDWARE_MISC),
    ("relay", ComponentCategory.HARDWARE_MISC),
    ("transformer", ComponentCategory.HARDWARE_MISC),
    ("inductor", ComponentCategory.HARDWARE_MISC),
    ("fuse", ComponentCategory.HARDWARE_MISC),
    ("lamp", ComponentCategory.HARDWARE_MISC),
    ("ferrite", ComponentCategory.HARDWARE_MISC),
    ("light depend", ComponentCategory.OPTOELECTRONICS),
    ("display", ComponentCategory.OPTOELECTRONICS),
    (
        "microcontroller",
        ComponentCategory.ICS,
    ),  # Tuner: 'ESP200  ESP32C3'  # Tuner: '7-segment LED display'
    ("terminal block", ComponentCategory.HARDWARE_MISC),
    ("switch", ComponentCategory.SWITCHES),
    ("slide", ComponentCategory.SWITCHES),  # 'DPDT slide, micro' (Aion FX Fusion)
    ("trimmer", ComponentCategory.POTENTIOMETERS),
    ("pot", ComponentCategory.POTENTIOMETERS),
    ("led", ComponentCategory.DIODES),
    ("diode", ComponentCategory.DIODES),
    ("transistor", ComponentCategory.TRANSISTORS),
    ("mosfet", ComponentCategory.TRANSISTORS),
    ("jfet", ComponentCategory.TRANSISTORS),
    ("operational amplifier", ComponentCategory.ICS),
    ("regulator", ComponentCategory.ICS),
    # Last: 'Light dependent resistor' is an LDR, 'Trimmer resistor' a pot.
    ("resistor", ComponentCategory.RESISTORS),
    ("capacitor", ComponentCategory.CAPACITORS),
)
# A named control in a grid: 'VOL', 'HI/LO', 'VOL 1', 'CLN_LVL'.
_CONTROL_LABEL = re.compile(r"[A-Z][A-Z0-9_/-]*(?: [A-Z0-9]+)?")
# Text tables are numbered page * this + header line, so two tables on one
# page stay apart.
_TABLES_PER_PAGE = 100
# Longer than any value: a headerless 'table' cell holding prose.
_PROSE_CHARS = 60
_SOCKET = re.compile(r"\bsocket\b", re.IGNORECASE)
_CHOOSE_ROW = re.compile(r"\s*choose between .+ or ", re.IGNORECASE)
_NUMBERED_NAME = re.compile(r"[A-Z]{1,3}\d{1,3}")
# A capacitor's voltage rating in a cell of its own: '63V'.
_RATING_CELL = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)
_TABLE_HEADERS = frozenset({"LOCATION", "QUANTITY", "VALUE", "TYPE"})
_LED_DESCRIPTION = re.compile(r"[^,]*\bLED\b", re.IGNORECASE)
_LED_SPECS = re.compile(r"LED\s*,\s*(.+)$", re.IGNORECASE)
_LED_COLOR = re.compile(
    r"\b(?:red|green|blue|yellow|white|orange|amber|purple|pink|uv)\b", re.IGNORECASE
)
# A VALUE cell that only abbreviates an LED: 'RED', 'Red 3mm', '3mm Red', 'LED'.
_LED_SHORTHAND = re.compile(
    r"(?i)(?:\d+(?:\.\d+)?mm\s*)?(?:(?:red|green|blue|yellow|white|orange|amber|purple|pink|uv)"
    r"\s*)?(?:LED\s*)?(?:\d+(?:\.\d+)?mm)?"
)
# Not preceded by "non-": a "Non-latching relay" is not latching.
_ACTUATION = re.compile(r"(?<!non-)(?<!non )\b(momentary|latching)\b", re.IGNORECASE)
_DUAL_GANG = re.compile(r"\bDUAL[- ]?GANG")
# 'ON/OFF/ON', or Aion FX's 'on-off-on' (normalized to slashes).
_POSITIONS = re.compile(r"\b(?:ON|OFF)(?:[/-](?:ON|OFF)){1,2}\b")
_CENTER_OFF = re.compile(r"\b(?:CENTER|CENTRE|CNTR\.?)[ -]OFF\b")
_TWO_POSITION = re.compile(r"\b2[- ]POSITION\b")
# Capacitor constructions a TYPE cell may name that the VALUE cell omits.
_DIELECTRICS = (("TANTALUM", "Tantalum"), ("MLCC", "MLCC"))


_VOLTAGE = re.compile(r"\b(\d+(?:\.\d+)?)\s*V(?:DC)?\b", re.IGNORECASE)
_NON_POLAR = re.compile(r"\bnon[- ]?polar(?:i[sz]ed)?\b|\bbi-?polar\b", re.IGNORECASE)
_NP_MARK = re.compile(r"\b(?:NP|BP)\b|\bnon[- ]?polar|\bbi-?polar", re.IGNORECASE)


def capacitor_notes(ref: str, value: str, notes: str) -> str:
    """Appends capacitor specs that only appear in the NOTES column.

    Taqueria lists C102 as '10u' with the note '25V Minimum Voltage', and Polar
    Cap lists C6 as '4u7' with '50V minimum, Non-Polar'; both the rating and
    the polarity are part of what must be bought.

    Args:
        ref: The row's designator.
        value: The (possibly enriched) VALUE cell.
        notes: The NOTES cell of the same row.

    Returns:
        For capacitors, the value with ' NP' appended when the notes call it
        non-polar, and '(<rating> minimum)' appended when the notes give a
        voltage; specs the value already states are not repeated.
    """
    designator = parse_designator(ref)
    if designator is None or designator.category != ComponentCategory.CAPACITORS:
        return value
    if _NON_POLAR.search(notes) and not _NP_MARK.search(value):
        value = f"{value} NP"
    rating = _VOLTAGE.search(notes)
    if rating is None or _VOLTAGE.search(value):
        return value
    return f"{value} ({rating.group(1)}V minimum)"


_RESTATED = re.compile(r"\s*\(\s*(\d+(?:\.\d+)?\s*[pnuµ])F?\s*\)$", re.IGNORECASE)


def _drop_restated_value(value: str) -> str:
    """Drops a parenthetical that restates the value in other units.

    Tone Zester lists C9 as '470n (0.47uF)'; the aside adds nothing to buy.
    """
    m = _RESTATED.search(value)
    if m is None:
        return value
    head = value[: m.start()]
    same = parse_value_to_decimal(head) == parse_value_to_decimal(m.group(1))
    return head if same else value


def category_from_type(type_text: str) -> ComponentCategory | None:
    """Category suggested by a TYPE column description ('Stereo Jack' -> Hardware).

    Hints match at word starts: 'red diffused' is not a fuse.
    """
    low = type_text.lower()
    return next(
        (cat for word, cat in _TYPE_HINTS if re.search(rf"\b{re.escape(word)}", low)),
        None,
    )


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
        code, rotary switch, switch positions, momentary/latching actuation, dual-gang pot,
        or capacitor construction (tantalum, MLCC) when the VALUE cell lacks one and the
        TYPE or NOTES cell provides it.
    """
    type_text = " ".join(type_text.split())
    detail = f"{type_text} {' '.join(notes.split())}".upper()
    specs = _LED_SPECS.match(type_text)
    if specs and _LED_SHORTHAND.fullmatch(value):
        # Aion FX: '5mm' / 'LED, 5mm, red diffused' lists the specs after 'LED,'.
        words = [w.strip() for w in specs.group(1).split(",") if w.strip()]
        sizes = [w for w in words if re.fullmatch(r"\d+(?:\.\d+)?mm", w)]
        rest = [w.title() for w in words if w not in sizes]
        return " ".join([*sizes, *rest, "LED"])
    led = _LED_DESCRIPTION.match(type_text)
    if led and _LED_SHORTHAND.fullmatch(value):
        # 'Red 3mm' / '3mm Red LED, Diffused' (Motorizer); 'RED' / '3mm LED, Red'
        # (Bolide) names the color only after the comma.
        description = led.group(0).strip()
        color = _LED_COLOR.search(value)
        if color and color.group(0).lower() not in description.lower():
            description = re.sub(
                r"\bLED\b", f"{color.group(0).capitalize()} LED", description, count=1
            )
        return description
    if not any(ch.isdigit() for ch in value) and not _POLE_THROW.search(value.upper()):
        # TYPE first ('Germanium diode, 1N34A'), then NOTES ('SANDED' /
        # '(JRC4558, etc)', Revision Overdrive).
        part = _PART_NUMBER.search(type_text) or _PART_NUMBER.search(notes)
        if part:
            return part.group(0)
        # 'RED' / '3mm Red LED, Diffused'; 'LED' / 'Indicator LED'.
        led = _LED_DESCRIPTION.match(type_text)
        if led:
            return led.group(0).strip()
    actuation = _ACTUATION.search(f"{type_text} {notes}")
    # Only a switch's actuation is chosen separately; a latching relay's part
    # number already says so ('TQ2-L-5V').
    if (
        actuation
        and "SWITCH" in detail
        and actuation.group(1).lower() not in value.lower()
    ):
        value = f"{value} {actuation.group(1).capitalize()}"
    # 'MOMENTARY' / 'Momentary SPST Footswitch' (Fornicus) names only the action.
    switch_value = constants.SWITCH_VALUE_PATTERN.search(value.upper()) or (
        _ACTUATION.fullmatch(value.strip()) and "SWITCH" in detail
    )
    if switch_value and not _POLE_THROW.search(value.upper()):
        pole = _POLE_THROW.search(type_text.upper())
        if pole:
            return f"{pole.group(0)} {value}"
    if _POLE_THROW.fullmatch(value.upper()) and "ROTARY" in detail:
        # '2P4T' / '16mm Mini Rotary switch, 2P4T' (Hwut Fuzz).
        return f"{value} {'Mini Rotary' if 'MINI ROTARY' in detail else 'Rotary'}"
    if _POLE_THROW.fullmatch(value.upper()):
        positions = _POSITIONS.search(detail)
        if positions:
            return f"{value} {positions.group(0).replace('-', '/')}"
        if _CENTER_OFF.search(detail):
            # 'SPDT' / 'Toggle switch, SPDT center off' (Aion FX Ephemeris).
            return f"{value} ON/OFF/ON"
        # 'Toggle switch, 2-position (SPDT)' (Maleficent Mids): two positions
        # are ON/ON; three could be ON/OFF/ON or ON/ON/ON, so are not guessed.
        if "TOGGLE" in detail and _TWO_POSITION.search(detail):
            return f"{value} ON/ON"
    if _DUAL_GANG.search(detail) and "DUAL" not in value.upper():
        return f"{value} Dual"
    if "CAPACITOR" in detail and any(ch.isdigit() for ch in value):
        for keyword, name in _DIELECTRICS:
            # 'Box/MLCC Capacitor' (Five Cats) leaves the choice open.
            either = re.search(rf"/\s*{keyword}|{keyword}\s*/", detail)
            if keyword in detail and keyword not in value.upper() and not either:
                return f"{value} {name}"
    return value


def _table_entries(
    pages_data: list[PDFPageExtraction],
    stats: StatsDict,
    only_pages: set[int] | None = None,
) -> list[Entry]:
    """Strategy 1: Extract entries from ruled tables (LOCATION / VALUE / TYPE / NOTES).

    Args:
        pages_data: Pre-extracted page data.
        stats: The statistics dictionary for tracking progress.
        only_pages: If given, the only pages to read.

    Returns:
        One entry per table row that names a designator and a value.
    """
    entries: list[Entry] = []
    table_no = -1
    for page_no, data in enumerate(pages_data):
        if only_pages is not None and page_no not in only_pages:
            continue
        for table in data["tables"]:
            if not table:
                continue
            table_no += 1

            grid = _value_column_grid(table, page_no, table_no)
            if grid is not None:
                entries += grid
                continue

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

            if loc_idx == -1 and {"QTY", "QUANTITY"} & set(headers):
                # ProtoBoard: 'QTY | PART NUMBER | DESCRIPTION' lists no designators.
                continue

            # Fallback mapping
            headerless = loc_idx == -1 or val_idx == -1
            if headerless:
                loc_idx, val_idx, start_row_idx = 0, 1, 0

            follows_or = False
            for row in table[start_row_idx:]:
                stats["lines_read"] += 1
                row_safe = [str(cell) if cell else "" for cell in row]
                filled = [c.strip() for c in row_safe if c.strip()]
                if filled and (
                    all(c.upper() == "OR" for c in filled)
                    # Spelunker: 'Choose between internal trimpot or external
                    # potentiometer, do not install both'.
                    or (len(filled) == 1 and _CHOOSE_ROW.match(filled[0]))
                ):
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
                        notes = (
                            row_safe[notes_idx]
                            if 0 <= notes_idx < len(row_safe)
                            else ""
                        )
                        val_raw, hint = _typed_row(
                            stats, ref_raw, val_raw, row_safe[type_idx], notes
                        )
                    # '100k (see text)' (GuitarPCB Mini-Me): the aside is no value.
                    val_raw = _drop_restated_value(strip_asides(val_raw))
                    if 0 <= notes_idx < len(row_safe):
                        val_raw = capacitor_notes(ref_raw, val_raw, row_safe[notes_idx])
                        if not _is_not_populated(val_raw):
                            check_version_note(stats, ref_raw, row_safe[notes_idx])
                elif len(row_safe) == 1 and row_safe[0]:
                    parts = row_safe[0].strip().split(None, 1)
                    if len(parts) == 2:
                        ref_raw, val_raw = parts[0].strip(), parts[1].strip()

                if headerless and len(val_raw) > _PROSE_CHARS:
                    # A page border drawn as a box reads as a one-cell 'table'
                    # holding the page's prose (GuitarPCB): not a parts row.
                    continue

                # Auditorium: 'TERMINAL BLOCK (Qty 3)'.
                qty = 1
                quantity = _REF_QUANTITY.search(ref_raw)
                if quantity:
                    qty = int(quantity.group(1))
                    ref_raw = ref_raw[: quantity.start()].strip()

                if ref_raw and val_raw:
                    entries.append(
                        Entry(
                            ref_raw,
                            val_raw,
                            page_no,
                            table=table_no,
                            hint=hint,
                            follows_or=follows_or,
                            qty=qty,
                        )
                    )
                    follows_or = False
    return entries


# The designator column of a value-column grid: 'Part', 'Part #', "MUFF'N Part #".
_GRID_REF_HEADER = re.compile(r"(?:^|\s)PART\s*#?$", re.IGNORECASE)
# Headers that name a column's role, not a circuit version.
_ROLE_HEADERS = frozenset(
    {
        "VALUE",
        "VALUES",
        "VAL",
        "TYPE",
        "NOTES",
        "NOTE",
        "QTY",
        "QUANTITY",
        "DESCRIPTION",
    }
)
# 'P1-Sus/Fuzz': the designator, then the control's name.
_NAMED_REF = re.compile(r"([A-Z]{1,3}\d{1,3})-(?![A-Z]{1,3}\d)[A-Za-z]")
# A note in a value column naming the row's role ('R12 | 1k8 | CLR', RATT).
_ROLE_CELLS = frozenset({"CLR"})


def _grid_header_text(cell: str | None) -> str:
    """A header cell as one line; a word wrapped mid-word ('Origina' / 'l') is rejoined."""
    lines = [line.strip() for line in str(cell or "").splitlines() if line.strip()]
    text = ""
    for line in lines:
        text += line if text and len(line) <= 2 else f" {line}"
    return text.strip()


def _value_column_grid(
    table: list[list[str | None]], page_no: int, table_no: int
) -> list[Entry] | None:
    """Reads a parts table with one value column per circuit version.

    GuitarPCB's MUFF'N prints 'MUFF'N Part # | Ram | Violet Ram | Triangle ...'
    and RATT Deluxe 'Part # | GPCB Values | Original': each row is a designator
    and its value in every version. An empty cell keeps the first column's
    value (RATT's original-Rat column leaves the board's added switches blank).

    Returns:
        One entry per designator and column, tagged with the column's name, or
        None when the table is not such a grid.
    """
    header_rows = [
        (k, cells)
        for k, row in enumerate(table[:3])
        if (cells := [_grid_header_text(c) for c in row])
        and _GRID_REF_HEADER.search(cells[0])
    ]
    if not header_rows:
        return None
    start, cells = header_rows[0]
    names = cells[1:]
    if len([n for n in names if n]) < 2 or any(
        n.upper() in _ROLE_HEADERS for n in names
    ):
        return None
    entries: list[Entry] = []
    for row in table[start + 1 :]:
        # 'FILTE' / 'R' is a wrapped 'FILTER' (RATT Deluxe).
        ref = _grid_header_text(row[0]).replace("*", "")
        named = _NAMED_REF.match(ref)
        if named:
            ref = named.group(1)
        if not ref or not (
            any(is_designator(r) for r in expand_refs(ref))
            or _CONTROL_LABEL.fullmatch(ref)
        ):
            continue
        cells = [" ".join(str(c or "").replace("*", " ").split()) for c in row[1:]]
        if any(c.split()[:1] == [ref] for c in cells if c):
            # A remark spanning the row: 'R24 | R24 is only available on v3 PCB
            # or earlier | omit ...' (MUFF'N).
            continue
        first = ""
        for k, name in enumerate(names):
            if not name:
                continue
            cell = cells[k] if k < len(cells) else ""
            if k == 0:
                first = cell
            elif not cell or cell.upper() in _ROLE_CELLS:
                cell = first
            if not cell or len(cell.split()) > 4:
                # Blank, or a remark spanning the row ('R24 is only available
                # on v3 PCB or earlier'): no part in this version.
                continue
            entries.append(Entry(ref, cell, page_no, table=table_no, column=name))
    return entries


def _typed_row(
    stats: StatsDict,
    ref: str,
    value: str,
    type_text: str,
    notes: str,
    *,
    check: bool = True,
) -> tuple[str, ComponentCategory | None]:
    """Completes a typed table row's value from its TYPE and NOTES cells.

    Shared by ruled and text tables (PedalPCB's 'LOCATION | VALUE | TYPE |
    NOTES', Aion FX's 'PART | VALUE | TYPE | NOTES').

    Args:
        stats: Stats object that receives warnings.
        ref: The designator cell.
        value: The VALUE cell.
        type_text: The TYPE cell.
        notes: The NOTES cell, if the table has one.
        check: Whether to report a row no category fits; text tables check
            each designator of a grouped cell themselves.

    Returns:
        The enriched value and the category the TYPE cell names, if any.
    """
    type_text = " ".join(type_text.split())
    # ElectroVibe: 'LDR1 | (blank) | Light Dependent Resistor |
    # (7530, 7532, 9203, or similar)' names the part in NOTES.
    choice = _NOTES_CHOICE.match(notes.strip())
    if not value and choice:
        value = choice.group(1)
    # Lone King: 'VOICE | (blank) | Toggle switch, SPDT | 3-position
    # (ON/OFF/ON)'; enrichment then adds the positions.
    pole = _POLE_THROW.search(type_text.upper())
    if not value and pole:
        value = pole.group(0)
    # Kickstart: 'SW1 | (blank) | 6mm x 6mm Push Button Switch'.
    if not value and type_text:
        value = type_text
    value = enrich_value_from_type(value, type_text, notes)
    hint = category_from_type(type_text)
    if check:
        _check_typed_row(stats, ref, value, hint)
    return value, hint


def _check_typed_row(
    stats: StatsDict, ref: str, value: str, hint: ComponentCategory | None
) -> None:
    """Reports a typed table row that no category fits (it must not vanish)."""
    if (
        ref[:1].isalpha()
        and value
        and value.upper() not in constants.NOT_POPULATED_VALUES
        # A repeated or garbled header row is not a part.
        and not {ref.upper(), value.upper()} & _TABLE_HEADERS
        and categorize_part(ref, value, hint) is None
    ):
        check_unclassified_row(stats, ref, value)


def _text_table_entries(
    pages_data: list[PDFPageExtraction], stats: StatsDict
) -> tuple[list[Entry], dict[tuple[int, int], float]]:
    """Strategy 1b: Extract entries from unruled 'Reference | Value | Quantity' tables.

    Each row may list several designators ('R10-R12,R18'); it becomes one
    entry per designator. A quantity that disagrees with the designator count
    is flagged, since one of the two must be wrong.

    Args:
        pages_data: Pre-extracted page data including word positions.
        stats: The statistics dictionary for tracking progress.

    Returns:
        One entry per designator, in reading order, and for each (page, line)
        read as a row, where the row ends (see `BomRow.end`).
    """
    entries: list[Entry] = []
    spans: dict[tuple[int, int], float] = {}
    seen: Counter[str] = Counter()
    for page_no, data in enumerate(pages_data):
        for row in find_bom_rows(data["words"]):
            stats["lines_read"] += 1
            value, hint = row.value, None
            refs = expand_refs(row.refs) if " " not in row.refs else [row.refs]
            if row.type_text:
                value, hint = _typed_row(
                    stats, row.refs, row.value, row.type_text, row.notes, check=False
                )
                if hint is None and not all(is_designator(ref) for ref in refs):
                    # A label the TYPE does not explain ('Screw | M3 x 5mm | For
                    # Nylon', Q-Tune's packing list) is left to the layout reader.
                    continue
                value = _drop_restated_value(value)
                value = capacitor_notes(row.refs, value, row.notes)
                # An empty spot can be filled in another build ('(omit) |
                # 100n for bass'), so its note matters too.
                check_version_note(stats, row.refs, row.notes)
                for ref in refs:
                    _check_typed_row(stats, ref, value, hint)
            if hint is not None:
                # A typed table describes every row, so a repeated label is
                # another part (Aion FX: GAIN pot and GAIN switch, Vulcan's
                # two R9s); it is bought too, under 'GAIN (2)'.
                refs = [_next_label(seen, ref) for ref in refs]
            check_row_quantity(stats, row.refs, len(refs), row.quantity)
            if hint is None:
                refs = _untyped_refs(stats, refs, value)
            for ref in refs:
                designator = parse_designator(ref)
                if designator and designator.category == ComponentCategory.CAPACITORS:
                    check_marking_code(stats, ref, value, row.marking)
            if refs:
                spans[page_no, row.line] = row.end
            entries += [
                Entry(
                    ref,
                    value,
                    page_no,
                    line=row.line,
                    x0=row.x0,
                    table=page_no * _TABLES_PER_PAGE + row.table,
                    hint=hint,
                )
                for ref in refs
            ]
    return entries, spans


def _untyped_refs(stats: StatsDict, refs: list[str], value: str) -> list[str]:
    """The parts of an untyped table row: designators and named controls.

    A 'Part | Value' grid lists controls beside designators ('VOL | A100k',
    GuitarPCB), which their value classifies. An odd name grouped with real
    designators ('R8, RPDo', Q-Tune) is reported; other labels (titles,
    footers, hardware such as 'Screw') are left to the layout reader.
    """
    if any(is_designator(ref) for ref in refs):
        for odd in [ref for ref in refs if not is_designator(ref)]:
            check_unclassified_row(stats, odd, value)
        return [ref for ref in refs if is_designator(ref)]
    return [
        ref
        for ref in refs
        if _CONTROL_LABEL.fullmatch(ref) and categorize_part(ref, value) is not None
    ]


def _without_note_cells(data: PDFPageExtraction, text: str) -> str:
    """Page text with typed tables' NOTES cells removed (see `bom_note_cells`)."""
    for cell in bom_note_cells(data["words"]):
        text = text.replace(cell.text, " ", 1)
    return text


# The number `_next_label` gives a repeated label: 'GAIN (2)'.
_REPEAT = re.compile(r" \(\d+\)$")


def _next_label(seen: Counter[str], ref: str) -> str:
    """The designator for a typed-table row, numbering repeats ('GAIN (2)').

    Repeats are counted per document: the tables continue across pages.
    """
    seen[ref.upper()] += 1
    return ref if seen[ref.upper()] == 1 else f"{ref} ({seen[ref.upper()]})"


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
    if DIP_SWITCH.search(value.upper()):
        return False  # a DIP switch is a part; a DIP socket line is not
    words = set(re.findall(r"[A-Z]+", value.upper()))
    phrase = " ".join(value.upper().split())
    return any(
        bad in words if " " not in bad else bad in phrase
        for bad in constants.IGNORE_VALUES
    )


def _layout_entries(
    pages_data: list[PDFPageExtraction],
    stats: StatsDict,
    table_values: dict[str, str],
    only_pages: set[int] | None = None,
    row_ends: dict[tuple[int, int], float] | None = None,
) -> list[Entry]:
    """Strategy 2: Extract entries from the page layout.

    Reads word positions to pair each designator with the value printed in the
    neighboring column (see `layout.py`). Designators the table strategy already
    found are skipped, so the two strategies never disagree about a part.

    Args:
        pages_data: Pre-extracted page data including word positions.
        stats: The statistics dictionary for tracking progress.
        table_values: Designators already extracted from tables, with their values.
        only_pages: If given, the only pages to read.
        row_ends: Lines already read as text-table rows, keyed (page, line),
            with where each row ends; listings beyond the end are read.

    Returns:
        One entry per listing, with its page position.
    """
    entries: list[Entry] = []
    for page_no, data in enumerate(pages_data):
        if only_pages is not None and page_no not in only_pages:
            continue
        # A ruled table's rows are read by the table strategy; read as layout,
        # 'D1 | LED | 3mm Red LED' looks like a bare 'LED' listing (Bad Mamma
        # Jamma), and a wrapped 'FILTE / R' cell like an 'R' control (RATT).
        # But the table may have read only the first of the listings side by
        # side on a line ('R2 | 1M | C2 | 220n' with its shaded rows merged
        # into 'R1 1M', NostalgiTone), so numbered designators other than the
        # row's own value are still read there.
        lines = page_lines(data["words"])
        table_lines = {
            k: set(table_values[cells[0].text].upper().split())
            for k, cells in enumerate(lines)
            if cells and cells[0].text in table_values
        }
        for listing in find_listings(data["words"]):
            stats["lines_read"] += 1
            # A range lists each of its designators: 'D1-D2  1N4148'.
            refs = (
                expand_refs(listing.ref)
                if REF_RANGE.fullmatch(listing.ref)
                else [listing.ref]
            )
            value = listing.value
            end = (row_ends or {}).get((page_no, listing.line), -1)
            for ref in refs:
                # A listing past the row's end is in a table beside it.
                row_value = (
                    None if listing.x0 >= end > 0 else table_lines.get(listing.line)
                )
                if (
                    ref in table_values
                    # 'Q1-Q5' read whole by the table (GoldenFalk).
                    or listing.ref in table_values
                    or (
                        row_value is not None
                        and (
                            not is_designator(ref)
                            or ref.upper() in row_value
                            or not _cell_value(lines[listing.line], listing)
                        )
                    )
                    or listing.x0 < end
                ):
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
                if (
                    _NUMBERED_NAME.fullmatch(ref)
                    and not is_designator(ref)
                    and any(ch.isdigit() for ch in value)
                    and any(ch.isalpha() for ch in value)  # not a bare quantity
                    and categorize_part(ref, value) is None
                ):
                    # Trumpeter's 'IQ1  2N5088' (a typo for Q1) is no known part,
                    # but is plainly listed as one.
                    check_unclassified_row(stats, ref, value)
                    continue
                entries.append(entry)
    return entries


def _cell_value(cells: list[Cell], listing: Listing) -> bool:
    """Whether a listing's value is just the cell after its designator.

    On a ruled table's line, a value that runs on over further cells has
    absorbed the next column ('C1 | 22n | P1-Reso | B50k Lin', GuitarPCB
    Swah). Only a voltage rating may follow ('C9 | 1n | 63V').
    """
    k = next((i for i, c in enumerate(cells) if c.x0 == listing.x0), None)
    if k is None:
        return False
    after = [c.text for c in cells[k + 1 : k + 3]]
    if not after or not clean_value(after[0]):
        return False  # 'C8A | * | 63V' (MoWah): the value is in the notes
    readings = [after[:1]]
    if len(after) == 2 and _RATING_CELL.fullmatch(after[1]):
        readings.append(after)
    return any(clean_value(" ".join(r)) == listing.value for r in readings if r)


def _ingest_entries(
    entries: list[Entry], source_name: str
) -> tuple[Inventory, StatsDict]:
    """Ingests one build's entries into a fresh inventory.

    Each build gets its own duplicate tracking, so two variants listing the
    same designator are not conflicts, while a conflict inside one build is.
    A designator listed again, with the same value, in another parts table of
    the same build belongs to another board (EV Mini's LFO and Audio PCBs
    both have Q1-Q4), so it is bought again rather than merged.
    """
    inventory = create_empty_inventory()
    stats = create_empty_stats()
    table_of: dict[str, int] = {}
    lists_sockets = any(_SOCKET.search(entry.value) for entry in entries)
    for entry in entries:
        if entry.not_populated:
            _record_not_populated(stats, entry.ref, entry.value)
            continue
        ref = entry.ref
        if entry.table is not None:
            first = table_of.setdefault(entry.ref, entry.table)
            seen = stats["seen_refs"].get(entry.ref)
            value = resolve_value_choice(None, entry.ref, entry.value)
            if first != entry.table and seen in (entry.value, value):
                del stats["seen_refs"][entry.ref]
            elif first != entry.table and seen is not None and not entry.follows_or:
                # Another table lists the same designator with another value:
                # another board (Tweed Fuzztone's Tweed and Fuzz boards both
                # have R1-R7), or another version. Both are bought, and the
                # builder is told which is which.
                ref = _next_board_ref(stats, entry.ref)
                check_second_board(stats, entry.ref, ref, seen, entry.value)
        stats["parts_found"] += ingest_bom_line(
            inventory,
            source_name,
            ref,
            entry.value,
            stats,
            entry.hint,
            entry.qty,
            inject=not lists_sockets,
        )
    return inventory, stats


def _next_board_ref(stats: StatsDict, ref: str) -> str:
    """The first free 'R1 (2)', 'R1 (3)' ... for a designator on another board."""
    n = 2
    while f"{ref} ({n})" in stats["seen_refs"]:
        n += 1
    return f"{ref} ({n})"


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


# Rotation terms this small are a PDF writer's rounding, not a rotation.
_SKEW_EPSILON = 1e-4


def _readable(obj: dict[str, Any]) -> bool:
    """Whether a PDF object is kept for text extraction.

    Characters printed upside down, or running bottom to top, extract in
    reverse: a vector schematic's '10k' reads 'k01' and '100uF' reads 'Fu001',
    which then look like parts lists (Aion FX legacy schematics). Nothing else
    is dropped.
    """
    if obj.get("object_type") != "char":
        return True
    matrix = obj.get("matrix")
    if not matrix:
        return True
    a, b, c, d = matrix[:4]
    # Only exact 180 and 90 degree rotations: text set along an arc (a
    # PedalPCB drill template's 'FOOTSWITCH') passes through every angle.
    upside_down = a < 0 and d < 0 and abs(b) < _SKEW_EPSILON and abs(c) < _SKEW_EPSILON
    # Text running bottom to top extracts reversed too ('Fu001' for '100uF').
    rising = abs(a) < _SKEW_EPSILON and abs(d) < _SKEW_EPSILON and b > 0 and c < 0
    return not (upside_down or rising)


def _page_words(page: Page) -> list[PDFWord]:
    return [
        PDFWord(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"], size=w["size"])
        for w in page.extract_words(x_tolerance=_WORD_GAP_PT, extra_attrs=["size"])
    ]


def _straighten(page: Page) -> Page:
    """Marks characters with negligible rotation as upright, and rounds sizes.

    Some GuitarPCB documents set every character with a rotation of about
    3e-8 and sizes that differ in the 6th decimal; pdfplumber then splits
    every letter into its own word and the parts list comes out letter-spaced
    ('R 1 0  7 5 0 R').
    """
    for char in page.chars:
        matrix = char.get("matrix")
        if (
            not char.get("upright")
            and matrix
            and matrix[0] > 0
            and matrix[3] > 0
            and abs(matrix[1]) < _SKEW_EPSILON
            and abs(matrix[2]) < _SKEW_EPSILON
        ):
            char["upright"] = True
            char["matrix"] = (matrix[0], 0.0, 0.0, *matrix[3:])
        if char.get("upright"):
            # Words split wherever the size differs, and sizes differ by float
            # noise. (Rotated labels keep theirs: joining them makes nonsense.)
            char["size"] = round(char["size"], 2)
    return page


def _without_marks(page: Page) -> Page:
    """The page without superscript footnote markers (see `footnote_marks`).

    Characters are dropped, not words, so ruled tables and page text lose the
    markers too.
    """
    marks = footnote_marks(_page_words(page))
    if not marks:
        return page

    def keep(obj: dict[str, Any]) -> bool:
        if obj.get("object_type") != "char":
            return True
        center = (obj["x0"] + obj["x1"]) / 2
        return not any(
            m["x0"] <= center <= m["x1"]
            and m["top"] <= obj["top"] + 0.5 <= m["top"] + m["size"]
            for m in marks
        )

    return page.filter(keep)


def read_pages(
    pages_data: list[PDFPageExtraction],
    source_name: str,
    stats: StatsDict,
    *,
    tables_only: bool = False,
) -> tuple[Inventory, tuple[BuildVariant, ...], tuple[OptionNote, ...]]:
    """Reads the parts lists of a document's pages and checks the result.

    The same readers serve a PDF's text layer and OCR'd page images (see
    `ocr.py`): both arrive as positioned words.

    Args:
        pages_data: Every page's tables, text and words, in document order.
        source_name: Label for the source.
        stats: Statistics and warnings, updated in place.
        tables_only: Whether each page holds nothing but its parts tables (OCR
            keeps only those), so every page is read, not only those with a
            text bill of materials.

    Returns:
        The single-build inventory (empty when the document has variants),
        the variants, and the option notes.
    """
    # --- STRATEGY 1: TABLE EXTRACTION ---
    # A text bill of materials is the document's parts list, so the
    # other strategies only read its own pages: elsewhere (vector
    # schematics) symbol labels look like listings and drawings like
    # tables.
    text_rows, row_ends = _text_table_entries(pages_data, stats)
    # Five Cats Tone Monarch heads its main table but not its daughterboard's.
    bom_pages = None if tables_only else ({row.page for row in text_rows} or None)
    entries = _table_entries(pages_data, stats, bom_pages)
    # A boxed 'Part | Value' grid is both a ruled table and a text table
    # (GuitarPCB); read it once, or every part would be bought twice.
    # The ruled cell keeps a value wrapped over two lines ('Bi-Color CA
    # LED (see' / 'text)', Mini-Me), so the ruled reading wins.
    # A repeated label is numbered by the text reading only ('MIX (2)',
    # Cataclysm lists MIX twice), so rows are matched by their label.
    ruled_refs = {(e.page, e.ref) for e in entries}
    text_rows = [
        r for r in text_rows if (r.page, _REPEAT.sub("", r.ref)) not in ruled_refs
    ]
    # Number the text tables after the ruled ones.
    entries += [
        replace(row, table=len(entries) + (row.table or 0)) for row in text_rows
    ]

    # --- STRATEGY 2: LAYOUT ANALYSIS ---
    # Recovers anything the tables missed (or everything, for column layouts)
    entries += _layout_entries(
        pages_data, stats, {e.ref: e.value for e in entries}, bom_pages, row_ends
    )

    # --- BUILDS: variants and options ---
    pages = [
        PageContext(page_lines(data["words"]), data["text"] or "")
        for data in pages_data
    ]
    plan = plan_builds(entries, pages)
    inventory, variants, options, union = _assemble_builds(plan, source_name, stats)

    # --- SELF-CHECK ---
    # Every designator listed in the document must be accounted for, in
    # some build or option.
    page_texts = [data["text"] or "" for data in pages_data]
    # Like the readers, a text bill of materials bounds where parts are
    # listed: Q-Tune's build steps mention 'the X1 connector area'.
    listed_texts = [
        _without_note_cells(data, text) if bom_pages is None or k in bom_pages else ""
        for k, (data, text) in enumerate(zip(pages_data, page_texts, strict=True))
    ]
    check_parts_found(stats, sum(len(d["refs"]) for d in union.values()))
    check_unextracted_refs(listed_texts, union, stats)
    check_footnotes(page_texts, stats)
    check_bom_reconciliation(page_texts, union, stats)
    return inventory, variants, options


# A footer sentence is no title: 'Don't forget to grab a copy of our general
# pedal building guide at: www.five-cats-pedals.co.uk/...' (Five Cats, whose
# only text is the footer).
_MAX_TITLE_WORDS = 8
_URL = re.compile(r"www\.|https?:", re.IGNORECASE)


def _is_title(text: str) -> bool:
    return len(text.split()) <= _MAX_TITLE_WORDS and not _URL.search(text)


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
            for raw_page in pdf.pages:
                page = _without_marks(_straighten(raw_page.filter(_readable)))
                pages_data.append(
                    {
                        # Same word gap as the layout reader: kerned cells
                        # otherwise run together ('3mmRedLED,Diffused').
                        "tables": page.extract_tables(
                            {"text_x_tolerance": _WORD_GAP_PT}
                        ),
                        "text": page.extract_text(),
                        "words": _page_words(page),
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
                    title = " ".join(w["text"] for w in title_words)
                    if _is_title(title):
                        stats["extracted_title"] = title
            except Exception:
                # Title extraction is heuristic/optional; don't fail the build if it misses.
                pass

            inventory, variants, options = read_pages(pages_data, source_name, stats)

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
