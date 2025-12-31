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


class PartData(TypedDict):
    qty: int
    refs: List[str]
    sources: Dict[str, List[str]]


InventoryType = Dict[str, PartData]


# Chip substitution recommendations
# Keys are the chips found in BOM, values are fun alternatives to try.
# Structure: (Part Name, Sonic Profile, Technical Why)
IC_ALTS = {
    # Dual Op-Amps
    "TL072": [
        (
            "OPA2134",
            "Hi-Fi / Studio Clean",
            "Low distortion (0.00008%), High Slew Rate (20V/us)",
        ),
        (
            "TLC2272",
            "High Headroom Clean",
            "Rail-to-Rail output (+6Vpp headroom on 9V)",
        ),
    ],
    "JRC4558": [
        (
            "NJM4558D",
            "Vintage Correct",
            "Authentic 1980s BJT bandwidth limiting",
        ),
        (
            "OPA2134",
            "Modern/Clinical",
            "High impedance input, removes 'warm' blur",
        ),
    ],
    # Single Op-Amps (RAT style)
    "LM308": [
        (
            "LM308N",
            "Vintage RAT",
            "Required for 0.3V/us slew-induced distortion",
        ),
        (
            "OP07",
            "Modern Tight",
            "Faster slew rate, sounds harsher/tighter than vintage",
        ),
    ],
    "NE5532": [
        (
            "OPA2134",
            "Lower Noise",
            "JFET input reduces current noise with high-Z guitars",
        ),
    ],
}

# Diode substitution recommendations
# Keys are the standard BOM parts, values are (Part, Sonic Profile, Technical Why)
DIODE_ALTS = {
    "1N4148": [
        (
            "1N4001",
            "Smooth / Tube-like",
            "Slow reverse recovery (30µs) smears highs",
        ),
        (
            "IR LED",
            "The 'Goldilocks' Drive",
            "1.2V drop: More crunch than LED, more headroom than Si",
        ),
        (
            "Red LED",
            "Amp-like / Open",
            "1.8V drop: Huge headroom, loud output",
        ),
    ],
    "1N914": [
        (
            "1N4001",
            "Smooth / Tube-like",
            "Slow reverse recovery (30µs) smears highs",
        ),
    ],
    "1N34A": [
        (
            "BAT41",
            "Modern Schottky",
            "Stable alternative, slightly harder knee",
        ),
        (
            "1N60",
            "Alt Germanium",
            "Different Vf variance",
        ),
    ],
}


def expand_refs(ref_raw: str) -> List[str]:
    """Explodes ranges like 'R1-R4' or 'R1-4' into ['R1', 'R2', 'R3', 'R4']."""
    refs = []
    ref_raw = ref_raw.strip()

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

    return refs


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


def parse_with_verification(
    bom_list: List[str], source_name: str = "Manual Input"
) -> Tuple[InventoryType, StatsDict]:
    """
    Parses raw text BOMs. Handles commas and ranges (R1-R4).
    """
    inventory: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )
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
                key = f"PCB | {clean_name}"
                inventory[key]["qty"] += 1
                inventory[key]["sources"][source_name].append("PCB")
                stats["parts_found"] += 1
                pcb_mode = False
                continue

            match = pattern.match(line)
            success = False

            if match:
                ref_raw = match.group(1).upper()
                val_raw = match.group(2)

                refs = expand_refs(ref_raw)

                # Process all refs found on this line
                line_has_part = False
                for r in refs:
                    cat, val, inj = categorize_part(r, val_raw)

                    if cat:
                        key = f"{cat} | {val}"
                        inventory[key]["qty"] += 1
                        inventory[key]["refs"].append(r)
                        inventory[key]["sources"][source_name].append(r)

                        if inj:
                            inventory[inj]["qty"] += 1
                            inventory[inj]["sources"][source_name].append(f"{r} (Inj)")

                        stats["parts_found"] += 1
                        line_has_part = True

                if line_has_part:
                    success = True

            if not success:
                stats["residuals"].append(line)

    return inventory, stats


def parse_csv_bom(filepath: str, source_name: str) -> Tuple[InventoryType, StatsDict]:
    """
    Parses a CSV file. Expects columns vaguely named 'Ref' and 'Value'.
    """
    inventory: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )
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
                expanded_refs = expand_refs(ref)

                for r in expanded_refs:
                    cat, clean_val, inj = categorize_part(r, val)
                    if cat:
                        key = f"{cat} | {clean_val}"
                        inventory[key]["qty"] += 1
                        inventory[key]["refs"].append(r)
                        inventory[key]["sources"][source_name].append(r)

                        if inj:
                            inventory[inj]["qty"] += 1
                            inventory[inj]["sources"][source_name].append(f"{r} (Inj)")

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
    if inventory["Hardware/Misc | SMD_ADAPTER_BOARD"]["qty"] > 0:
        warnings.append(
            "⚠️  SMD ADAPTERS: Added for MMBF5457. Check if your PCB has SOT-23 pads first."
        )
    if inventory["Hardware/Misc | 8 PIN DIP SOCKET"]["qty"] > 0:
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

        if spec_type == "MLCC":
            return f"{val} multilayer"

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

    # Specific override for JRC4558 (Vintage/Obsolete Name) -> NJM4558 (Modern Name)
    if "JRC4558" in val.upper():
        return "NJM4558D"

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
                note_parts.append("Rec: Class 1 Ceramic (C0G / NP0)")
            else:
                note_parts.append(f"Rec: {spec_type}")

            # Join properly
            note = " | ".join(note_parts)

    elif category == "Diodes":
        buy = max(10, count + 5)
        # Check for Texture Upgrades
        if val in DIODE_ALTS:
            alts = DIODE_ALTS[val]
            txt_parts = []
            for item in alts:
                # Robust unpacking for 2-tuple (Legacy) or 3-tuple (Expert)
                c, d = item[0], item[1]
                t = item[2] if len(item) > 2 else None

                if t:
                    txt_parts.append(f"{c} ({d}: {t})")
                else:
                    txt_parts.append(f"{c} ({d})")

            note_txt = ", ".join(txt_parts)
            note = f"💡 TRY: {note_txt}"

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
            txt_parts = []
            for item in alts:
                # Robust unpacking for 2-tuple (Legacy) or 3-tuple (Expert)
                c, d = item[0], item[1]
                t = item[2] if len(item) > 2 else None

                if t:
                    txt_parts.append(f"{c} ({d}: {t})")
                else:
                    txt_parts.append(f"{c} ({d})")

            txt = ", ".join(txt_parts)
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


def sort_inventory(inventory: InventoryType) -> List[Tuple[str, PartData]]:
    """Sorts parts by Category Rank, THEN by Physical Value (Ohms/Farads)."""
    order = [
        "PCB",
        "ICs",
        "Transistors",
        "Diodes",
        "Potentiometers",
        "Switches",
        "Capacitors",
        "Resistors",
        "Hardware/Misc",
    ]
    # Map name to index for sorting
    pmap = {name: i for i, name in enumerate(order)}

    def sort_key(item: Tuple[str, PartData]) -> Tuple[int, float, str]:
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


def get_standard_hardware(inventory: InventoryType, pedal_count: int = 1) -> None:
    """
    Mutates the inventory in-place to add Missing/Critical hardware.
    """

    def inject(category: str, val: str, qty_per_pedal: int, note: str):
        """Standardizes the injection logic."""
        # Ensure we match the formatting of the main parser
        key = f"{category} | {val}"

        total_qty = qty_per_pedal * pedal_count

        # In-place mutation
        inventory[key]["qty"] += total_qty
        inventory[key]["refs"].append("HW")  # Generic ref for hardware

        # Track source with the note for context
        source_tag = f"Auto-Inject ({note})"
        inventory[key]["sources"]["Auto-Inject"].append(source_tag)

    # 1. SMART MERGE ITEMS (Check for existence or inject)

    # Resistor 3.3k (For LED CLR)
    # Note: We inject into the main Resistors category so it sorts correctly
    inject("Resistors", "3.3k", 1, "LED CLR")

    # LED
    inject("Diodes", "LED", 1, "Status Light")

    # 2. FORCED HARDWARE ITEMS

    # Germanium Logic
    if any("FUZZ" in k.upper() for k in inventory if k.startswith("PCB")):
        inject("Transistors", "Germanium PNP", 0, "Vintage Option")

    inject("Hardware/Misc", "1590B Enclosure", 1, "Verify PCB fit")
    inject("Hardware/Misc", "3PDT FOOTSWITCH PCB", 1, "Wiring Board")
    inject("Hardware/Misc", "3PDT STOMP SWITCH", 1, "Blue/Standard")
    inject("Hardware/Misc", "6.35MM JACK (STEREO)", 1, "Input")
    inject("Hardware/Misc", "6.35MM JACK (MONO)", 1, "Output")
    inject("Hardware/Misc", "DC POWER JACK 2.1MM", 1, "Center Negative")
    inject("Hardware/Misc", "Bezel LED Holder", 1, "3mm Metal")
    inject("Hardware/Misc", "Rubber Feet (Black)", 4, "Enclosure Feet")
    inject("Hardware/Misc", "AWG 24 Hook-Up Wire", 3, "Approx 1ft/pedal")
    inject("Hardware/Misc", "9V BATTERY CLIP", 1, "Optional")
    inject("Hardware/Misc", "Heat Shrink Tubing", 1, "Insulation")

    # Knobs (Dynamic Count)
    total_pots = sum(
        d["qty"] for k, d in inventory.items() if k.startswith("Potentiometers")
    )
    # We divide by pedal_count to get pots per pedal, then re-multiply inside inject.
    # Alternatively, we just pass the raw total if we treat it as a bulk injection.
    # Let's keep the logic consistent:
    if total_pots > 0:
        # We inject the TOTAL quantity calculated from the inventory
        key = "Hardware/Misc | Knob"
        inventory[key]["qty"] += total_pots
        inventory[key]["sources"]["Auto-Inject"].append(f"Knobs ({total_pots})")

        key_seal = "Hardware/Misc | Dust Seal Cover"
        inventory[key_seal]["qty"] += total_pots
        inventory[key_seal]["sources"]["Auto-Inject"].append(
            f"Pot Seals ({total_pots})"
        )


def parse_pedalpcb_pdf(
    filepath: str, source_name: str
) -> Tuple[InventoryType, StatsDict]:
    """
    Parses a PedalPCB Build Document (PDF).
    Extracts the BOM table using visual line detection.
    """
    inventory: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )
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
                        expanded_refs = expand_refs(ref_raw)
                        row_parsed = False  # Reset flag for this row

                        for r in expanded_refs:
                            cat, clean_val, inj = categorize_part(r, val_raw)
                            if cat:
                                row_parsed = True  # We found at least one valid part!

                                key = f"{cat} | {clean_val}"
                                inventory[key]["qty"] += 1
                                inventory[key]["refs"].append(r)
                                inventory[key]["sources"][source_name].append(r)

                                if inj:
                                    inventory[inj]["qty"] += 1
                                    inventory[inj]["sources"][source_name].append(
                                        f"{r} (Inj)"
                                    )
                                stats["parts_found"] += 1

                        # If the loop finishes and we never found a valid part category:
                        if not row_parsed:
                            stats["residuals"].append(f"| {ref_raw} | {val_raw} |")

    except Exception as e:
        stats["residuals"].append(f"PDF Parse Error: {e}")

    return inventory, stats
