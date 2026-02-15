"""
File ingestion and parsing logic for BOMs and Inventory.

This module handles the extraction of component data from various file formats
(PDF, CSV, Text). It orchestrates the line-by-line reading, cleaning, and
delegation to the classification engine.
"""

import csv
import logging
import re

import src.bom_lib.constants as C
from src.bom_lib.classifier import categorize_part, normalize_value_by_category
from src.bom_lib.manager import _record_part
from src.bom_lib.types import InventoryType, StatsDict, create_empty_inventory
from src.bom_lib.utils import expand_refs

# Initialize Logger
logger = logging.getLogger(__name__)


def ingest_bom_line(
    inventory: InventoryType,
    source: str,
    ref_raw: str,
    val_raw: str,
    stats: StatsDict | None = None,
) -> int:
    """
    Core ingestion kernel: Expands refs, classifies, and updates inventory.

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

    Returns:
        The number of valid parts successfully found and recorded.
    """
    parts_found = 0
    expanded_refs = expand_refs(ref_raw)

    for r in expanded_refs:
        # De-dupe Check
        if stats is not None and "seen_refs" in stats:
            if r in stats["seen_refs"]:
                continue
            stats["seen_refs"].add(r)

        cat, clean_val, inj = categorize_part(r, val_raw)

        if cat:
            parts_found += 1
            main_key = f"{cat} | {clean_val}"

            # 1. Record Main Part
            _record_part(inventory, source, main_key, r)

            # 2. Handle Auto-Injection (e.g., Sockets)
            if inj:
                # inj is pre-formatted as "Category | Value"
                _record_part(inventory, source, inj, f"{r} (Inj)")

    return parts_found


def parse_with_verification(
    bom_list: list[str], source_name: str = "Manual Input"
) -> tuple[InventoryType, StatsDict]:
    """
    Parses a list of raw text strings (Manual BOM Input).

    Handles standard formats like "R1 10k" and special cases like "PCB Name".

    Args:
        bom_list: List of strings (lines from a text area).
        source_name: Label for the source of these parts.

    Returns:
        A tuple of (Updated Inventory, Parsing Statistics).
    """
    inventory = create_empty_inventory()
    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": set(),
        "errors": [],
    }

    # Regex: Matches Ref + Separator + Value.
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)[\s,]+([0-9a-zA-Z\.\-\/]+).*")

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
                key = f"PCB | {clean_name}"

                inventory[key]["qty"] += 1
                if "PCB" not in inventory[key]["sources"][source_name]:
                    inventory[key]["sources"][source_name].append("PCB")

                stats["parts_found"] += 1
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
                    key = f"PCB | {line.strip()}"
                    inventory[key]["qty"] += 1
                    if "PCB" not in inventory[key]["sources"][source_name]:
                        inventory[key]["sources"][source_name].append("PCB")
                    stats["parts_found"] += 1
                else:
                    stats["residuals"].append(line)

    return inventory, stats


def parse_csv_bom(filepath: str, source_name: str) -> tuple[InventoryType, StatsDict]:
    """
    Parses a CSV BOM file.

    Attempts to intelligently guess columns ('Ref', 'Value') or falls back
    to using the first two columns.

    Args:
        filepath: Path to the CSV file.
        source_name: Label for the source.

    Returns:
        A tuple of (Updated Inventory, Parsing Statistics).
    """
    inventory = create_empty_inventory()
    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": set(),
        "errors": [],
    }

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

    return inventory, stats


def parse_user_inventory(filepath: str) -> InventoryType:
    """
    Parses a user's stock CSV.

    Expects columns: Category, Part, Qty.
    Applies the same value normalization as BOM parsing to ensure keys match.

    Args:
        filepath: Path to the user inventory CSV.

    Returns:
        A populated InventoryType dictionary.
    """
    stock: InventoryType = create_empty_inventory()

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
                clean_val = normalize_value_by_category(cat, val)
                key = f"{cat} | {clean_val}"

                _record_part(stock, "User Stock", key, ref="", qty=qty)

    return stock


def parse_pedalpcb_pdf(
    filepath: str, source_name: str
) -> tuple[InventoryType, StatsDict]:
    """
    Parses a PedalPCB Build Document (PDF).

    Uses a multi-stage strategy:
    1. Visual Table Extraction (via pdfplumber lines).
    2. Text-based heuristic layout analysis.
    3. Regex "Hail Mary" pass for difficult files.

    Args:
        filepath: Path to the PDF file.
        source_name: Label for the source.

    Returns:
        A tuple of (Updated Inventory, Parsing Statistics).
    """
    # Lazy import to avoid loading heavy PDF libraries unless needed
    import pdfplumber

    inventory = create_empty_inventory()
    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": set(),
        "errors": [],
    }

    try:
        with pdfplumber.open(filepath) as pdf:
            # --- TITLE EXTRACTION (Page 1) ---
            try:
                p1 = pdf.pages[0]
                words = p1.extract_words(extra_attrs=["size"])
                ignore = {
                    "PEDALPCB",
                    "CONTROLS",
                    "REVISION",
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
                pass

            # --- STRATEGY 1: TABLE EXTRACTION ---
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue

                    # Header mapping
                    headers = [str(h).upper().strip() for h in table[0] if h]
                    loc_idx = -1
                    val_idx = -1
                    start_row_idx = 1

                    for i, h in enumerate(headers):
                        if h in ("LOCATION", "REF", "DESIGNATOR", "PART"):
                            loc_idx = i
                        elif h in ("VALUE", "VAL", "DESCRIPTION"):
                            val_idx = i

                    # Fallback mapping
                    if loc_idx == -1 or val_idx == -1:
                        loc_idx, val_idx, start_row_idx = 0, 1, 0

                    for row in table[start_row_idx:]:
                        stats["lines_read"] += 1
                        row_safe = [str(cell) if cell else "" for cell in row]

                        # Skip Summary lines (e.g., "1 x 100k")
                        first_content = next((c for c in row_safe if c), "")
                        if re.match(r"^\d+\s*[xX]", first_content):
                            continue

                        ref_raw = ""
                        val_raw = ""

                        if len(row_safe) > max(loc_idx, val_idx):
                            ref_raw = row_safe[loc_idx].replace("\n", " ").strip()
                            val_raw = row_safe[val_idx].replace("\n", " ").strip()
                        elif len(row_safe) == 1 and row_safe[0]:
                            parts = row_safe[0].strip().split(None, 1)
                            if len(parts) == 2:
                                ref_raw, val_raw = parts[0].strip(), parts[1].strip()

                        if ref_raw and val_raw:
                            count = ingest_bom_line(
                                inventory, source_name, ref_raw, val_raw, stats
                            )
                            if count > 0:
                                stats["parts_found"] += count

            # --- STRATEGY 2: REGEX FALLBACK ---
            kw_regex_str = "|".join([rf"\b{k}\b" for k in C.KEYWORDS])

            # Scope: If nothing found yet, match everything.
            # If parts found, restrict to Keywords (controls) to avoid noise.
            if stats["parts_found"] == 0:
                ref_pattern = rf"(?P<ref>\b[A-Z]{{1,4}}\d+\b|{kw_regex_str})"
            else:
                ref_pattern = rf"(?P<ref>{kw_regex_str})"

            regex = re.compile(rf"{ref_pattern}\s+(?P<val>[^\s]+)", re.IGNORECASE)

            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                matches = list(regex.finditer(text))

                for i, match in enumerate(matches):
                    ref_str = match.group("ref").upper()
                    val_str = match.group("val")
                    val_start = match.start("val")

                    # Lookahead safely
                    next_match_start = (
                        matches[i + 1].start() if i + 1 < len(matches) else len(text)
                    )

                    # Expand value capture for multi-word items (Switches, Pots)
                    if (
                        ref_str.startswith("LDR")
                        or ref_str in C.KEYWORDS
                        or ref_str.startswith(("POT", "VR"))
                    ):
                        line_end = text.find("\n", val_start)
                        if line_end == -1:
                            line_end = len(text)
                        cutoff = min(line_end, next_match_start)
                        val_str = text[val_start:cutoff].strip()

                    # Clean brackets
                    if (val_str.startswith("(") and val_str.endswith(")")) or (
                        val_str.startswith("[") and val_str.endswith("]")
                    ):
                        val_str = val_str[1:-1].strip()

                    # Filters
                    if len(val_str) > 50 or len(val_str) < 1:
                        continue
                    if any(bad in val_str.upper() for bad in C.IGNORE_VALUES):
                        continue
                    if re.match(r"^(is|see|note)\s", val_str, re.IGNORECASE):
                        continue
                    if re.match(r"^\d{1,2}[\.\-\/]\d{1,2}[\.\-\/]\d{2,4}", val_str):
                        continue

                    # Validation
                    is_keyword = ref_str in C.KEYWORDS
                    if not is_keyword:
                        # Must start with valid prefix
                        valid_prefixes = C.CORE_PREFIXES + ("POT", "VR", "L", "LD")
                        if not any(ref_str.startswith(p) for p in valid_prefixes):
                            continue
                        # "Ghost Data" check (Qty Part reversed)
                        if len(ref_str) >= 3 and re.match(r"^\d+$", val_str):
                            continue
                    else:
                        # Keyword validation
                        has_digit = any(char.isdigit() for char in val_str)
                        is_switch = any(
                            x in val_str.upper()
                            for x in ["SPDT", "DPDT", "3PDT", "ON/ON", "ON/OFF"]
                        )
                        if not has_digit and not is_switch:
                            continue

                        # Semantic Check (e.g., prevent "LOOP" -> "IC")
                        val_up = val_str.upper()
                        bad_starts = (
                            "IC",
                            "DIP",
                            "SOIC",
                            "PKG",
                            "MODULE",
                            "PCB",
                            "TL",
                            "OP",
                            "NE5",
                        )
                        is_package = "DIP" in val_up or "SOIC" in val_up
                        if any(val_up.startswith(x) for x in bad_starts) or (
                            is_package and not has_digit
                        ):
                            continue

                    c = ingest_bom_line(inventory, source_name, ref_str, val_str, stats)
                    if c > 0:
                        stats["parts_found"] += c

    except Exception as e:
        stats["residuals"].append(f"PDF Parse Error: {e}")

    return inventory, stats
