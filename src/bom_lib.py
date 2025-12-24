import re
import csv
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, TypedDict


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
        injection = "Hardware/Misc | 8_PIN_DIP_SOCKET"

    # Only normalize Passives (Resistors/Caps) to avoid mangling Transistors
    # (e.g., preventing "2N5457" from becoming "2")
    if category in ("Resistors", "Capacitors"):
        fval = parse_value_to_float(val_clean)
        if fval is not None:
            val_clean = float_to_search_string(fval)
            val_up = val_clean.upper()

    # SMD Substitution Logic (J201/2N5457 -> SMD Adapter)
    if "2N5457" in val_up:
        val_clean = "MMBF5457"
        injection = "Hardware/Misc | SMD_ADAPTER_BOARD"
    elif "MMBF5457" in val_up:
        injection = "Hardware/Misc | SMD_ADAPTER_BOARD"

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
    if inventory.get("Hardware/Misc | 8_PIN_DIP_SOCKET", 0) > 0:
        warnings.append(
            "ℹ️  IC SOCKETS: Added sockets for chips. Optional but recommended."
        )
    return warnings


def get_buy_details(category: str, val: str, count: int) -> Tuple[int, str]:
    """Applies 'Nerd Economics' to calculate buy quantity."""
    buy = count
    note = ""

    if category == "Resistors":
        buy = max(10, count + 5)
    elif category == "Capacitors":
        if "100n" in val.lower() or "0.1u" in val.lower():
            buy = count + 10
            note = "Power filtering (buy bulk)."
        else:
            buy = count + 3
    elif category == "Diodes":
        buy = max(10, count + 5)
    elif category == "Transistors":
        if "MMBF" in val:
            buy = count + 5
            note = "SMD SUB for 2N5457. Needs adapter!"
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
                if reduced.is_integer():
                    return f"{int(reduced)}{suffix}"
                return f"{reduced:.1f}{suffix}"

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
