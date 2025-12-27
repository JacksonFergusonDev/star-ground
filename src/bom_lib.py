import re
import csv
import math
import pdfplumber
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, TypedDict
from urllib.parse import quote_plus


# SI Prefix Multipliers
MULTIPLIERS = {
    "p": 1e-12,  # pico
    "n": 1e-9,  # nano
    "u": 1e-6,  # micro (standard)
    "µ": 1e-6,  # micro (alt)
    "m": 1e-3,  # milli
    "k": 1e3,  # kilo
    "K": 1e3,  # kilo (uppercase tolerance)
    "M": 1e6,  # Mega
    "G": 1e9,  # Giga
}


# --- Type Definitions ---
class StatsDict(TypedDict):
    lines_read: int
    parts_found: int
    residuals: List[str]


InventoryType = Dict[str, int]


# Chip substitution recommendations
# Keys are the chips found in BOM, values are fun alternatives to try.
IC_ALTS = {
    # Dual Op-Amps
    "TL072": [("JRC4558", "Vintage warmth"), ("NE5532", "Low noise/Hi-Fi")],
    "JRC4558": [("TL072", "Modern/Clear"), ("NE5532", "Hi-Fi")],
    # Single Op-Amps (RAT style)
    "LM308": [
        ("OP07", "Modern stable equiv"),
        ("TL071", "High fidelity (changes tone)"),
    ],
    "OP07": [("LM308", "Vintage original"), ("TL071", "Bright mod")],
}


def categorize_part(
    ref: str, val: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Decides what a part is based on its Ref (Name) and Value.
    """
    ref_up = ref.upper().strip()  # Designators (R1) are always upper
    val_clean = val.strip()  # Keep original case for display
    val_up = val_clean.upper()  # Use this for internal logic

    # 1. Known Potentiometer Labels
    # If the ref matches these, it's definitely a knob.
    pot_labels = {
        "POT",
        "TRIM",
        "VR",
        "VOL",
        "VOLUME",
        "TONE",
        "GAIN",
        "DRIVE",
        "DIST",
        "FUZZ",
        "DIRT",
        "LEVEL",
        "MIX",
        "BLEND",
        "DRY",
        "WET",
        "SPEED",
        "RATE",
        "DEPTH",
        "INTENSITY",
        "WIDTH",
        "DECAY",
        "ATTACK",
        "RELEASE",
        "SUSTAIN",
        "COMP",
        "THRESH",
        "TREBLE",
        "BASS",
        "MID",
        "MIDS",
        "PRESENCE",
        "CONTOUR",
        "EQ",
        "BODY",
        "BIAS",
        "BOOST",
        "MASTER",
        "PRE",
        "POST",
        "FILTER",
        "RANGE",
        "SENS",
    }

    # 2. Standard Component Prefixes
    # Note: 'P' or 'POT' are handled above.
    valid_prefixes = ("R", "C", "D", "Q", "U", "IC", "SW", "OP", "TL")

    # 3. Taper Check (The "Smart" Check)
    # Looks for "B100k", "10k-A" to identify pots by value.
    is_pot_value = False
    if re.search(r"[0-9]+.*[ABCWG]$", val_up) or re.search(r"^[ABCWG][0-9]+", val_up):
        is_pot_value = True

    # Validity Check
    is_valid = (
        any(ref_up.startswith(p) for p in valid_prefixes)
        or ref_up in pot_labels
        or any(ref_up.startswith(label) for label in pot_labels)
        or is_pot_value
    )

    if not is_valid:
        return None, None, None

    # Classification
    category = "Unknown"
    injection: Optional[str] = None

    # Check Pots first (avoids collisions like 'RANGE' starting with 'R')
    if (
        ref_up in pot_labels
        or any(ref_up.startswith(label) for label in pot_labels)
        or is_pot_value
    ):
        category = "Potentiometers"

    elif ref_up.startswith("R") and not ref_up.startswith("RANGE"):
        category = "Resistors"
    elif ref_up.startswith("C"):
        category = "Capacitors"
    elif ref_up.startswith("D"):
        category = "Diodes"
    elif ref_up.startswith("Q"):
        category = "Transistors"
    elif ref_up.startswith("SW"):
        category = "Switches"

    # ICs -> Inject Socket
    elif ref_up.startswith(("U", "IC", "OP", "TL")):
        category = "ICs"
        injection = "Hardware/Misc | 8 PIN DIP SOCKET"

    # Only normalize Passives (Resistors/Caps) to avoid mangling Transistors
    # (e.g., preventing "2N5457" from becoming "2")
    if category in ("Resistors", "Capacitors"):
        fval = parse_value_to_float(val_clean)
        if fval is not None:
            val_clean = float_to_search_string(fval)
            val_up = val_clean.upper()

    return category, val_clean, injection


def parse_with_verification(bom_list: List[str]) -> Tuple[InventoryType, StatsDict]:
    """
    Parses raw text BOMs. Handles commas and ranges (R1-R4).
    """
    inventory: InventoryType = defaultdict(int)
    stats: StatsDict = {"lines_read": 0, "parts_found": 0, "residuals": []}

    # Regex: Matches Ref + Separator + Value.
    # Separator can be whitespace or comma.
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)[\s,]+([0-9a-zA-Z\.\-\/]+).*")

    for raw_text in bom_list:
        lines = raw_text.strip().split("\n")
        pcb_mode = False

        for line in lines:
            line = line.strip()
            if not line:
                continue
            stats["lines_read"] += 1

            # Catch "PCB" header lines
            if line.upper() == "PCB":
                pcb_mode = True
                continue
            if pcb_mode:
                clean_name = re.sub(r"^PCB\s+", "", line, flags=re.IGNORECASE).strip()
                inventory[f"PCB | {clean_name}"] += 1
                stats["parts_found"] += 1
                pcb_mode = False
                continue

            match = pattern.match(line)
            success = False

            if match:
                ref_raw = match.group(1).upper()
                val_raw = match.group(2)

                refs: List[str] = []

                # Handle Ranges (R1-R5)
                if "-" in ref_raw:
                    try:
                        # Matches R1-R4 or R1-4
                        m = re.match(r"([A-Z]+)(\d+)-([A-Z]+)?(\d+)", ref_raw)
                        if m:
                            prefix = m.group(1)
                            start = int(m.group(2))
                            end = int(m.group(4))

                            if (end - start) < 50:  # Sanity check
                                for i in range(start, end + 1):
                                    refs.append(f"{prefix}{i}")
                            else:
                                refs.append(ref_raw)
                        else:
                            refs.append(ref_raw)
                    except Exception:
                        refs.append(ref_raw)
                else:
                    refs.append(ref_raw)

                # Process all refs found on this line
                line_has_part = False
                for r in refs:
                    cat, val, inj = categorize_part(r, val_raw)

                    if cat:
                        inventory[f"{cat} | {val}"] += 1
                        if inj:
                            inventory[inj] += 1
                        stats["parts_found"] += 1
                        line_has_part = True

                if line_has_part:
                    success = True

            if not success:
                stats["residuals"].append(line)

    return inventory, stats


def parse_csv_bom(filepath: str) -> Tuple[InventoryType, StatsDict]:
    """
    Parses a CSV file. Expects columns vaguely named 'Ref' and 'Value'.
    """
    inventory: InventoryType = defaultdict(int)
    stats: StatsDict = {"lines_read": 0, "parts_found": 0, "residuals": []}

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["lines_read"] += 1
            # Lowercase keys to find columns easier
            row_clean = {k.lower(): v for k, v in row.items() if k}

            ref = (
                row_clean.get("ref")
                or row_clean.get("designator")
                or row_clean.get("part")
            )
            val = (
                row_clean.get("value")
                or row_clean.get("val")
                or row_clean.get("description")
            )

            success = False
            if ref and val:
                cat, clean_val, inj = categorize_part(ref, val)
                if cat:
                    inventory[f"{cat} | {clean_val}"] += 1
                    if inj:
                        inventory[inj] += 1
                    stats["parts_found"] += 1
                    success = True

            if not success:
                stats["residuals"].append(str(row))

    return inventory, stats


def get_residual_report(stats: StatsDict) -> List[str]:
    """Returns lines that look like parts but were skipped."""
    safe_words = [
        "RESISTORS",
        "CAPACITORS",
        "TRANSISTORS",
        "DIODES",
        "POTENTIOMETERS",
        "PCB",
        "COMPONENT LIST",
        "SOCKET",
    ]
    suspicious: List[str] = []

    for line in stats["residuals"]:
        upper = line.upper()
        is_header = any(w in upper for w in safe_words)

        # If it's not a header but has numbers, it might be a missed part
        if not is_header and any(c.isdigit() for c in line):
            suspicious.append(line)

    return suspicious


def get_injection_warnings(inventory: InventoryType) -> List[str]:
    """Warns user if we made assumptions (SMD adapters, Sockets)."""
    warnings = []
    if inventory.get("Hardware/Misc | SMD_ADAPTER_BOARD", 0) > 0:
        warnings.append(
            "⚠️  SMD ADAPTERS: Added for MMBF5457. Check if your PCB has SOT-23 pads first."
        )
    if inventory.get("Hardware/Misc | 8 PIN DIP SOCKET", 0) > 0:
        warnings.append(
            "ℹ️  IC SOCKETS: Added sockets for chips. Optional but recommended."
        )
    return warnings


def get_spec_type(category: str, val: str) -> str:
    """
    Determines the specific material or type based on category and value.
    Used for Search Generation and Recommendation Notes.
    """
    if category == "Capacitors":
        fval = parse_value_to_float(val)
        if fval is None:
            return ""

        # Pico/Nano Range (<= 1nF)
        if fval < 1.0e-9:
            return "MLCC"

        # Film Range (1nF < val < 1uF)
        elif 1.0e-9 <= fval < 1.0e-6:
            return "Box Film"

        # The Ambiguous 1uF Crossover (== 1uF)
        elif abs(fval - 1.0e-6) < 1.0e-9:
            return "Box Film"

        # The Power Range (> 1uF)
        else:
            return "Electrolytic"

    return ""


def generate_search_term(category: str, val: str, spec_type: str = "") -> str:
    """
    Generates a Tayda-optimized search string.
    """
    if category == "Resistors":
        return f"{val} ohm 1/4w metal film"

    elif category == "Capacitors":
        # Check if it ends in a shorthand unit (p, n, u) and append 'F'
        if val and val[-1] in "pnu":
            val += "F"

        if spec_type:
            return f"{val} {spec_type}"
        return val

    elif category == "Potentiometers":
        # 1. Determine Taper
        taper = "Linear"  # Default
        val_upper = val.upper()

        if "A" in val_upper:
            taper = "Logarithmic"
        elif "B" in val_upper:
            taper = "Linear"
        elif "C" in val_upper:
            taper = "Reverse Log"
        elif "W" in val_upper:
            taper = "W Taper"

        # 2. Clean Value (e.g. "B100k" -> "100k")
        # Strip taper letters so the float parser can find the number
        clean_raw = re.sub(r"[ABCW\-\s]", "", val_upper)
        fval = parse_value_to_float(clean_raw)

        if fval is not None:
            clean_val = float_to_search_string(fval)
        else:
            clean_val = clean_raw if clean_raw else val

        return f"{clean_val} ohm {taper} potentiometer"

    elif category == "Diodes":
        # "LED" is too generic; default to a standard indicator
        if val.upper() == "LED":
            return "LED 3mm"
        return val

    # Specific override for Sockets to get the solder type
    if val == "8 PIN DIP SOCKET":
        return "8 pin DIP IC Socket Adaptor Solder Type"

    # Default / Pass-through (ICs, Hardware, PCB, Switches)
    return val


def generate_tayda_url(search_term: str) -> str:
    """
    Creates a clickable search link for Tayda Electronics.
    """
    if not search_term:
        return ""

    encoded = quote_plus(search_term)
    return f"https://www.taydaelectronics.com/catalogsearch/result/?q={encoded}"


def get_buy_details(category: str, val: str, count: int) -> Tuple[int, str]:
    """Applies 'Nerd Economics' to calculate buy quantity."""
    buy = count
    note = ""

    # --- Sanity Check (Suspicious Physics) ---
    # We re-parse the value here to check for physics anomalies.
    fval = parse_value_to_float(val)

    if category == "Resistors":
        # Nerd Economics: Buffer +5, then round up to nearest 10 (Tayda pack size)
        buffered_qty = count + 5
        buy = math.ceil(buffered_qty / 10) * 10

        # Default Material Recommendation
        note = "Use 1/4W Metal Film (1%)"

        # Warn if < 1 Ohm
        if fval is not None and fval < 1.0:
            note = "⚠️ Suspicious Value (< 1Ω). Verify BOM."

    elif category == "Capacitors":
        is_bypass_cap = False
        if fval is not None:
            # Check if value is approx 1.0e-7 (allow small float drift)
            if abs(fval - 1.0e-7) < 1.0e-9:
                is_bypass_cap = True

        # Explicitly type hint the list to keep mypy happy
        note_parts: List[str] = []

        if is_bypass_cap:
            buy = count + 10
            note_parts = ["Power filtering (buy bulk)."]
        else:
            buy = count + 3
            note_parts = []

        # Warn if > 10,000uF (0.01F) - Likely a parsing error (e.g. "1F")
        if fval is not None and fval > 0.01:
            note_parts.append("⚠️ Suspicious Value (> 10mF).")

        # MATERIAL RECOMMENDATIONS
        spec_type = get_spec_type(category, val)
        if spec_type:
            # Preserve the specific 1uF warning logic while using the shared type
            if (
                spec_type == "Box Film"
                and fval is not None
                and abs(fval - 1.0e-6) < 1.0e-9
            ):
                note_parts.append("Rec: Box Film (Check BOM: Could be Electrolytic)")
            elif spec_type == "MLCC":
                note_parts.append("Rec: Monolithic Ceramic (MLCC)")
            else:
                note_parts.append(f"Rec: {spec_type}")

            # Join properly
            note = " | ".join(note_parts)

    elif category == "Diodes":
        buy = max(10, count + 5)

    elif category == "Transistors":
        # User asked for the obsolete THT part
        if "2N5457" in val.upper():
            buy = count + 3
            note = "⚠️ Obsolete part! Check for speciality vendors or consider MMBF5457."

        # Case B: User asked for the SMD part
        elif "MMBF" in val.upper():
            buy = count + 5
            note = "SMD Part! Verify PCB pads or buy adapter."
        else:
            buy = count + 3

    elif category == "ICs":
        buy = count + 1
        note = "Audio Chip (Socket added)"
        # Suggest mods
        clean = re.sub(r"(CP|CN|P|N)$", "", val)
        if clean in IC_ALTS:
            alts = IC_ALTS[clean]
            txt = ", ".join([f"{c} ({d})" for c, d in alts])
            note += f" | 💡 TRY: {txt}"

    elif category == "Hardware/Misc":
        if "ADAPTER" in val:
            buy = count + 4
            note = "[AUTO] Verify PCB pads."
        elif "SOCKET" in val:
            buy = count + 2
            note = "[AUTO] For chip safety."
        else:
            buy = count + 1

    elif category == "PCB":
        note = "Main Board"

    return buy, note


def sort_inventory(inventory: InventoryType) -> List[Tuple[str, int]]:
    """Sorts parts by Category Rank, THEN by Physical Value (Ohms/Farads)."""
    order = [
        "PCB",
        "ICs",
        "Transistors",
        "Diodes",
        "Potentiometers",
        "Switches",
        "Hardware/Misc",
        "Capacitors",
        "Resistors",
    ]
    # Map name to index for sorting
    pmap = {name: i for i, name in enumerate(order)}

    def sort_key(item: Tuple[str, int]) -> Tuple[int, float, str]:
        key = item[0]
        if " | " not in key:
            return (999, 0.0, key)

        cat, val = key.split(" | ", 1)
        rank = pmap.get(cat, 100)

        # Parse value for sorting
        fval = parse_value_to_float(val)
        if fval is None:
            fval = 0.0

        return (rank, fval, val)

    return sorted(inventory.items(), key=sort_key)


def parse_value_to_float(val_str: str) -> Optional[float]:
    """
    Reduces component values to their base SI unit (Ohms/Farads).
    Handles: '10k', '4.7u', '100', '1M'
    Returns: float or None if parsing fails.
    """
    if not val_str:
        return None

    val_str = val_str.strip()

    # 1. Handle "Sandwich" notation (BS 1852): 1k5 -> 1500.0
    # Match: (Digits)(Multiplier)(Digits)
    sandwich = re.match(r"^(\d+)([pnuµmkKMG])(\d+)", val_str)

    if sandwich:
        whole = sandwich.group(1)
        suffix = sandwich.group(2)
        fraction = sandwich.group(3)

        # Reassemble as float: 1k5 -> 1.5 * 1000
        base = float(f"{whole}.{fraction}")
        return base * MULTIPLIERS[suffix]

    # 2. Standard "Number + Suffix"
    # Match: (Start)(Number)(Multiplier?)(Everything Else)
    match = re.search(r"^([\d\.]+)\s*([pnuµmkKMG])?", val_str.strip())

    if match:
        num_str = match.group(1)
        suffix = match.group(2)

        try:
            base_val = float(num_str)
        except ValueError:
            return None

        if suffix and suffix in MULTIPLIERS:
            return base_val * MULTIPLIERS[suffix]

        return base_val

    return None


def float_to_search_string(val: float) -> str:
    """
    Machine-readable format (e.g., 1500.0 -> '1.5k').
    Used for: Searching Tayda, sorting keys.
    """
    if val is None:
        return ""

    # Determine the best prefix
    # iterate high to low to find the first one that fits
    # (p, n, u, m, k, M, G)
    # We use a reduced subset for display to keep it clean
    for suffix, multiplier in [("M", 1e6), ("k", 1e3)]:
        if val >= multiplier:
            # Check if it's a whole number after division
            reduced = val / multiplier
            reduced = round(reduced, 6)  # Avoid float precision issues

            if reduced.is_integer():
                return f"{int(reduced)}{suffix}"

            return f"{reduced:.1f}{suffix}"  # 4.7k

    # Cap/Inductor logic (u, n, p)
    # This logic is a bit purely mathematical,
    # but works for standard E12 series values.
    if val < 1.0:
        for suffix, multiplier in [("u", 1e-6), ("n", 1e-9), ("p", 1e-12)]:
            if val >= multiplier:
                reduced = val / multiplier
                reduced = round(reduced, 6)  # Avoid float precision issues
                if reduced.is_integer():
                    return f"{int(reduced)}{suffix}"
                return f"{reduced:.1f}{suffix}"

    val = round(val, 6)  # Avoid float precision issues
    # Fallback for plain ohms (100R)
    if val.is_integer():
        return str(int(val))
    return str(val)


def float_to_display_string(val: float) -> str:
    """
    Human-readable BS 1852 format (e.g., 1500.0 -> '1k5').
    Used for: The printed checklist.
    """
    base = float_to_search_string(val)

    # Transform 4.7k -> 4k7
    if "." in base and any(c.isalpha() for c in base):
        # Split by the decimal
        num, rest = base.split(".")
        # Rest contains '7k'
        # We want to find the letter and sandwich it
        match = re.search(r"(\d+)([a-zA-Z]+)", rest)
        if match:
            decimal_part = match.group(1)
            suffix = match.group(2)
            return f"{num}{suffix}{decimal_part}"

    return base


def get_standard_hardware(inventory: InventoryType, pedal_count: int = 1) -> List[dict]:
    """
    Generates the 'Missing/Critical' section based on pedal count and pot count.
    """
    hardware = []

    # --- INTERNAL HELPERS ---
    def _create_entry(
        category: str,
        part_name: str,
        qty: int,
        note: str,
        section: str,
        search_val: Optional[str] = None,
        buy_qty: Optional[int] = None,
    ):
        """Helper to build consistent hardware rows with search links."""
        # Use the specific search value (e.g. "3.3k") if provided, otherwise use the part name
        if search_val is None:
            search_val = part_name

        # Default buy_qty to BOM qty if not specified
        if buy_qty is None:
            buy_qty = qty

        search_term = generate_search_term(category, search_val)
        url = generate_tayda_url(search_term)

        hardware.append(
            {
                "Section": section,
                "Category": category,
                "Part": part_name,
                "BOM Qty": qty,
                "Buy Qty": buy_qty,
                "Notes": note,
                "Search Term": search_term,
                "Tayda_Link": url,
            }
        )

    def smart_merge(category, val, part_display, note, section="Missing/Critical"):
        """Checks inventory state before injecting."""
        key = f"{category} | {val}"

        if key in inventory:
            # IT EXISTS: Just bump the count.
            inventory[key] += 1 * pedal_count
        else:
            # MISSING: Add to list.
            bom_qty = 1 * pedal_count
            calc_buy_qty = bom_qty

            if category == "Resistors":
                # Buffer +5, Round to nearest 10
                calc_buy_qty = math.ceil((bom_qty + 5) / 10) * 10
            elif category == "Diodes":  # LED
                calc_buy_qty = bom_qty + 2

            _create_entry(
                category,
                part_display,
                bom_qty,
                note,
                section,
                search_val=val,
                buy_qty=calc_buy_qty,
            )

    def add_forced(
        part,
        qty,
        note="",
        section="Missing/Critical",
        category="Hardware",
        search_val: Optional[str] = None,
        buy_qty: Optional[int] = None,
    ):
        """Always injects the item."""
        _create_entry(
            category, part, qty, note, section, search_val=search_val, buy_qty=buy_qty
        )

    # --- 1. SMART MERGE ITEMS ---
    # Resistor 3.3k (For LED CLR)
    smart_merge("Resistors", "3.3k", "3.3k", "For LED CLR")

    # LED
    smart_merge("Diodes", "LED", "LED (Diffuse)", "Status Light")

    # --- 2. ALWAYS MISSING ITEMS ---

    p = pedal_count

    add_forced("1590B Enclosure", 1 * p, "Standard size. Verify PCB fit!")

    add_forced(
        "3PDT FOOTSWITCH PCB",
        1 * p,
        "Tayda Wiring Board",
        search_val="3PDT Footswitch DIY PCB Wiring Board",
    )

    add_forced("3PDT STOMP SWITCH", 1 * p, "Blue/Standard")

    add_forced(
        "6.35MM JACK (STEREO)",
        1 * p,
        "Input (Stereo handles battery)",
        search_val="6.35MM JACK STEREO",
    )

    add_forced("6.35MM JACK (MONO)", 1 * p, "Output", search_val="6.35MM JACK MONO")

    add_forced("DC POWER JACK 2.1MM", 1 * p, "Standard Center Negative")

    add_forced(
        "Bezel LED Holder",
        1 * p,
        "Match LED size (3mm) | Rec: Metal",
        search_val="3mm Bezel LED Holder Metal",
    )

    add_forced(
        "Rubber Feet (Black)", 4 * p, "Enclosure Feet", search_val="Rubber Feet Black"
    )

    add_forced("AWG 24 Hook-Up Wire", 3 * p, "Approx 1ft (30cm) per pedal")

    add_forced("9V BATTERY CLIP", 1 * p, "Optional", "Recommended Extras")

    add_forced(
        "Heat Shrink Tubing",
        1 * p,
        "Essential for insulation",
        "Recommended Extras",
        search_val="Heat Shrink Tubing 2.5mm",
    )

    # Knobs (Dynamic Count)
    total_pots = sum(c for k, c in inventory.items() if k.startswith("Potentiometers"))
    if total_pots > 0:
        add_forced("Knob", total_pots, "1 per Pot")
        add_forced(
            "Dust Seal Cover",
            total_pots,
            "Protects pots",
            "Recommended Extras",
            buy_qty=total_pots + 2,
        )

    return hardware


def parse_pedalpcb_pdf(filepath: str) -> Tuple[InventoryType, StatsDict]:
    """
    Parses a PedalPCB Build Document (PDF).
    Extracts the BOM table using visual line detection.
    """
    inventory: InventoryType = defaultdict(int)
    stats: StatsDict = {"lines_read": 0, "parts_found": 0, "residuals": []}

    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()

                for table in tables:
                    # Header Check
                    # Row 0 usually contains ["LOCATION", "VALUE", "TYPE", "NOTES"]
                    if not table:
                        continue

                    # Normalized headers to find columns
                    # Handle cases where headers might be None or empty strings
                    headers = [str(h).upper().strip() for h in table[0] if h]

                    # Heuristic: Must have LOCATION and VALUE columns to be a BOM
                    if "LOCATION" not in headers or "VALUE" not in headers:
                        continue

                    # Map headers to list indices
                    try:
                        loc_idx = headers.index("LOCATION")
                        val_idx = headers.index("VALUE")
                    except ValueError:
                        continue

                    # Process Rows (Skip header)
                    for row in table[1:]:
                        stats["lines_read"] += 1

                        # Handle potential None cells or short rows
                        row_safe = [str(cell) if cell else "" for cell in row]

                        # Skip if row is too short to contain the data
                        if len(row_safe) <= max(loc_idx, val_idx):
                            continue

                        # Clean up newlines inside cells (e.g. "Resistor\n1/4W")
                        ref_raw = row_safe[loc_idx].replace("\n", " ").strip()
                        val_raw = row_safe[val_idx].replace("\n", " ").strip()

                        if not ref_raw or not val_raw:
                            continue

                        # Categorize
                        cat, clean_val, inj = categorize_part(ref_raw, val_raw)
                        if cat:
                            inventory[f"{cat} | {clean_val}"] += 1
                            if inj:
                                inventory[inj] += 1
                            stats["parts_found"] += 1
                        else:
                            # Log failed rows from the table as residuals for debugging
                            stats["residuals"].append(f"| {ref_raw} | {val_raw} |")

    except Exception as e:
        stats["residuals"].append(f"PDF Parse Error: {e}")

    return inventory, stats
