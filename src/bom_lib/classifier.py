"""
Component classification and heuristic categorization logic.

This module determines what a component *is* (e.g., Resistor, Potentiometer, IC)
based on its reference designator (R1, U1) and value. It also normalizes
values to ensure consistent matching between BOMs and Inventory.
"""

import re

import src.bom_lib.constants as C
from src.bom_lib import float_to_search_string, parse_value_to_float


def normalize_value_by_category(category: str, val_raw: str) -> str:
    """
    Standardizes component values for consistent string matching.

    For passives (Resistors/Caps), this converts raw strings like "10,000" or
    "10K" into a canonical format "10k" using the utility parser.

    Args:
        category: The determined component category (e.g., "Resistors").
        val_raw: The raw value string from the BOM (e.g., "100n").

    Returns:
        A normalized string (e.g., "100n") or the original string if
        normalization isn't applicable.
    """
    clean_val = val_raw.strip()

    # Only normalize Passives (Resistors/Caps)
    if category in ("Resistors", "Capacitors"):
        # Exception: Don't normalize physical dimensions (e.g., "5mm LDR")
        if "mm" in clean_val.lower():
            return clean_val

        fval = parse_value_to_float(clean_val)
        if fval is not None:
            clean_val = float_to_search_string(fval)

    return clean_val


def categorize_part(ref: str, val: str) -> tuple[str | None, str | None, str | None]:
    """
    Classifies a component based on its Reference Designator and Value.

    This function acts as a rules engine. It checks standard prefixes (R, C, Q),
    identifies potentiometers by known names (VOL, GAIN) or taper markings
    (A100k), and handles special cases like LDRs.

    Args:
        ref: The reference designator (e.g., "R1", "VOLUME", "IC1").
        val: The component value (e.g., "10k", "TL072").

    Returns:
        A tuple containing:
            - category: The standardized category string (e.g., "Resistors").
            - clean_val: The normalized value string.
            - injection: An optional string describing a secondary part to
              inject (e.g., "Hardware/Misc | DIP SOCKET").
        Returns (None, None, None) if the part is invalid or ignored.
    """
    ref_up = ref.upper().strip()
    val_clean = val.strip()  # Keep original case for display
    val_up = val_clean.upper()  # Use this for internal logic

    # 1. Validate Prefix / Structure
    valid_prefixes = C.CORE_PREFIXES + ("OP", "TL", "LDR", "LED")

    # Standard components (R1, C1) usually require a digit.
    # Named controls (VOLUME, SW_BRIGHT) do not.
    has_digit = any(char.isdigit() for char in ref_up)

    # 2. Taper Check (Potentiometer Heuristic)
    # Detects pots by value style (e.g., "B100k", "10k-A") if the ref isn't obviously a chip.
    is_pot_value = False
    if not ref_up.startswith(("IC", "U", "Q", "OP", "TL")):
        taper_chars = "".join(C.POT_TAPER_MAP.keys())
        # Matches "B100k" or "100k-B"
        if re.search(rf"[0-9]+.*[{taper_chars}]$", val_up) or re.search(
            rf"^[{taper_chars}][0-9]+", val_up
        ):
            is_pot_value = True

    # 3. Validity Check
    is_valid = (
        (any(ref_up.startswith(p) for p in valid_prefixes) and has_digit)
        or ref_up in C.POT_LABELS
        or ref_up in C.SWITCH_LABELS
        or any(ref_up.startswith(label) for label in C.POT_LABELS)
        or is_pot_value
        or ref_up == "CLR"
    )

    if not is_valid:
        return None, None, None

    # 4. Classification Logic
    category = "Unknown"
    injection: str | None = None

    # LDR Exception (Light Dependent Resistor)
    if ref_up.startswith("LDR"):
        return "Optoelectronics", val_clean, None

    # Potentiometers (Priority over Resistors to catch 'RANGE')
    if (
        ref_up in C.POT_LABELS
        or any(ref_up.startswith(label) for label in C.POT_LABELS)
        or is_pot_value
    ):
        category = "Potentiometers"

    # Switches
    elif ref_up in C.SWITCH_LABELS:
        # Ambiguity check: "LENGTH" could be a switch or a pot.
        if any(x in val_up for x in ["ON", "SW", "SP", "DP"]):
            category = "Switches"
        else:
            category = "Potentiometers"  # Fallback

    # Standard Components
    elif ref_up == "CLR" or (ref_up.startswith("R") and not ref_up.startswith("RANGE")):
        category = "Resistors"
    elif ref_up.startswith("C"):
        category = "Capacitors"
    elif ref_up.startswith("D"):
        category = "Diodes"
    elif ref_up.startswith("Q"):
        category = "Transistors"
    elif ref_up.startswith("SW"):
        category = "Switches"
    elif ref_up.startswith("LED"):
        category = "Diodes"
    elif ref_up.startswith(("X", "Y")):
        category = "Crystals/Oscillators"
    elif ref_up.startswith("J"):
        category = "Hardware/Misc"

    # ICs & Socket Injection
    elif ref_up.startswith(("U", "IC", "OP", "TL")):
        category = "ICs"

        # Don't inject sockets for things that aren't DIP chips
        # (e.g., Regulators, Reverb Bricks)
        skip_injection_keywords = ["REGULATOR", "L78L", "MODULE", "BTDR", "REVERB"]
        if not any(k in val_up for k in skip_injection_keywords):
            injection = "Hardware/Misc | DIP SOCKET (Check Size)"

    # Final Normalization
    val_clean = normalize_value_by_category(category, val_clean)

    return category, val_clean, injection
