"""
Business logic for sourcing, purchasing, and hardware injection.

This module contains the "Nerd Economics" logic, which includes:
- Calculating safe buy quantities (buffers for small parts).
- Generating search terms for suppliers (e.g., Tayda).
- Injecting required hardware (jacks, switches) that isn't on the PCB BOM.
- Identifying missing or residual parts from parsing.
"""

import math
import re
from urllib.parse import quote_plus

import src.bom_lib.constants as C
from src.bom_lib.types import InventoryType, StatsDict
from src.bom_lib.utils import float_to_search_string, parse_value_to_float


def get_residual_report(stats: StatsDict) -> list[str]:
    """
    Identifies potential parts hidden in the parser's rejected lines.

    Scans the 'residuals' (unparsed lines) for text that looks like a part
    but was missed by the regex. Useful for debugging parsing errors.

    Args:
        stats: The statistics dictionary containing residuals.

    Returns:
        A list of suspicious lines that might require manual review.
    """
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
    suspicious: list[str] = []

    for line in stats["residuals"]:
        # Pass explicit errors/exceptions through
        if "ERROR" in line.upper() or "EXCEPTION" in line.upper():
            suspicious.append(line)
            continue

        upper = line.upper()
        is_header = any(w in upper for w in safe_words)

        # If it's not a header but has numbers, it might be a missed part
        if not is_header and any(c.isdigit() for c in line):
            suspicious.append(line)

    return suspicious


def get_injection_warnings(inventory: InventoryType) -> list[str]:
    """
    Generates user warnings based on automated hardware injections.

    Args:
        inventory: The current inventory state.

    Returns:
        A list of warning strings (e.g., checking SMD adapters).
    """
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
    Determines the specific capacitor dielectric or material type.

    Used to refine search terms (e.g., distinguishing MLCC from Electrolytic
    based on capacitance).

    Args:
        category: Component category.
        val: Component value string.

    Returns:
        A string describing the type (e.g., "MLCC", "Box Film", "Electrolytic"),
        or an empty string if not applicable.
    """
    if category == "Capacitors":
        fval = parse_value_to_float(val)
        if fval is None:
            return ""

        # <= 1nF -> Ceramic/MLCC
        if fval < 1.0e-9:
            return "MLCC"

        # 1nF to 1uF -> Film
        elif 1.0e-9 <= fval < 1.0e-6 or abs(fval - 1.0e-6) < 1.0e-9:
            return "Box Film"

        # > 1uF -> Electrolytic
        else:
            return "Electrolytic"

    return ""


def generate_search_term(category: str, val: str, spec_type: str = "") -> str:
    """
    Generates a supplier-optimized search string.

    Targeted primarily at Tayda Electronics' search engine behavior.

    Args:
        category: Component category.
        val: Component value.
        spec_type: Optional material type (from get_spec_type).

    Returns:
        A search string (e.g., "100k ohm Linear potentiometer").
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
        taper = "Linear"  # Default
        val_upper = val.upper()
        is_dual = "DUAL" in val_upper or "STEREO" in val_upper

        for code, name in C.POT_TAPER_MAP.items():
            if code in val_upper:
                taper = name
                break

        # Clean "B100k" -> "100k"
        taper_chars = "".join(C.POT_TAPER_MAP.keys())
        clean_raw = re.sub(rf"[{taper_chars}\-\s]", "", val_upper)
        fval = parse_value_to_float(clean_raw)

        if fval is not None:
            clean_val = float_to_search_string(fval)
        else:
            clean_val = clean_raw if clean_raw else val

        base_term = f"{clean_val} ohm {taper} potentiometer"
        return f"Dual Gang {base_term}" if is_dual else base_term

    elif category == "Diodes":
        if val.upper() == "LED":
            return "LED 3mm"
        return val

    # Overrides
    if val == "8 PIN DIP SOCKET":
        return "8 pin DIP IC Socket Adaptor Solder Type"
    if "JRC4558" in val.upper():
        return "NJM4558D"

    return val


def generate_tayda_url(search_term: str) -> str:
    """Generates a clickable search URL for Tayda Electronics."""
    if not search_term:
        return ""
    encoded = quote_plus(search_term)
    return f"https://www.taydaelectronics.com/catalogsearch/result/?q={encoded}"


def generate_pedalpcb_url(search_term: str) -> str:
    """Generates a clickable search URL for PedalPCB."""
    if not search_term:
        return ""
    clean_term = search_term.replace(" PCB", "").strip()
    encoded = quote_plus(clean_term)
    return f"https://www.pedalpcb.com/?product_cat=&s={encoded}&post_type=product"


def get_buy_details(
    category: str, val: str, count: int, fval: float | None = None
) -> tuple[int, str]:
    """
    Calculates the purchase quantity and notes based on 'Nerd Economics'.

    Applies logic to buffer small parts (resistors), enforce exact counts
    for expensive parts (ICs), and suggest substitutions or warnings.

    Args:
        category: Component category.
        val: Component value.
        count: The raw net need (BOM Qty - Stock Qty).

    Returns:
        A tuple containing:
            - buy: The integer quantity to purchase.
            - note: A string containing warnings, recommendations, or subs.
    """
    if count <= 0:
        return 0, ""

    buy = count
    note = ""

    # Fallback if fval wasn't passed (for backward compatibility or tests)
    if fval is None:
        fval = parse_value_to_float(val)

    if category == "Resistors":
        rules = C.PURCHASING_CONFIG["Resistors"]

        buffered_qty = count + rules["buffer_add"]
        round_step = rules["round_to"]
        buy = math.ceil(buffered_qty / round_step) * round_step

        note = rules["note"]
        if fval is not None and fval < rules["suspicious_threshold_low"]:
            note = "⚠️ Suspicious Value (< 1Ω). Verify BOM."

    elif category == "Optoelectronics":
        buy = count + 1  # Fragile legs

    elif category == "Capacitors":
        note_parts: list[str] = []
        buffer = 5

        # Bypass caps (100nF) -> Bulk buy
        if fval is not None and abs(fval - 1.0e-7) < 1.0e-9:
            buffer = 10
            note_parts.append("Power filtering (buy bulk).")
        # Large caps (> 1uF) -> Low buffer
        elif fval is not None and fval >= 1.0e-6:
            buffer = 1

        buy = count + buffer
        if fval is not None and fval > 0.01:
            note_parts.append("⚠️ Suspicious Value (> 10mF).")

        spec_type = get_spec_type(category, val)
        if spec_type:
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
        note = " | ".join(note_parts)

    elif category == "Diodes":
        buy = max(10, count + 5)
        # Check substitutions
        if val in C.DIODE_ALTS:
            alts = C.DIODE_ALTS[val]
            txt_parts = [
                f"{item[0]} ({item[1]}{': ' + item[2] if len(item) > 2 else ''})"
                for item in alts
            ]
            note = f"💡 TRY: {', '.join(txt_parts)}"

    elif category == "Transistors":
        buy = count + 1
        if "2N5457" in val.upper():
            note = "⚠️ Obsolete part! Check for speciality vendors or consider MMBF5457."
        elif "MMBF" in val.upper():
            note = "SMD Part! Verify PCB pads or buy adapter."

    elif category == "ICs":
        buy = count
        note = "Socket Recommended"
        clean_ic = re.sub(r"(CP|CN|P|N)$", "", val)
        if clean_ic in C.IC_ALTS:
            alts = C.IC_ALTS[clean_ic]
            txt_parts = [
                f"{item[0]} ({item[1]}{': ' + item[2] if len(item) > 2 else ''})"
                for item in alts
            ]
            note += f" | 💡 TRY: {', '.join(txt_parts)}"

    elif category == "Crystals/Oscillators":
        buy = count + 1
        note = "Heat sensitive / Fragile"

    elif category == "Hardware/Misc":
        if "ADAPTER" in val or "SOCKET" in val:
            buy = count + 1
            note = (
                "[AUTO] Verify PCB pads."
                if "ADAPTER" in val
                else "[AUTO] For chip safety."
            )
        else:
            buy = count

    elif category == "PCB":
        note = "Main Board"

    return buy, note


def get_standard_hardware(inventory: InventoryType, pedal_count: int = 1) -> None:
    """
    Injects standard enclosure hardware into the inventory.

    Adds items like jacks, switches, DC sockets, and wiring that are almost
    never listed on the PCB BOM but are required to build the pedal.

    Args:
        inventory: The inventory dictionary to mutate in-place.
        pedal_count: Number of pedals being built (multiplier for hardware).
    """

    def inject(
        category: str,
        val: str,
        qty_per_pedal: int,
        note: str,
        qty_override: int | None = None,
    ):
        key = f"{category} | {val}"
        total_qty = (
            qty_override if qty_override is not None else (qty_per_pedal * pedal_count)
        )

        inventory[key]["qty"] += total_qty
        inventory[key]["refs"].append("HW")
        inventory[key]["sources"]["Auto-Inject"].append(f"Auto-Inject ({note})")

    # 1. Smart Merges (Add to existing categories)
    inject("Resistors", "3.3k", 1, "LED CLR")
    inject("Diodes", "LED", 1, "Status Light")

    # 2. Germanium Heuristic (Fuzz check)
    if any("FUZZ" in k.upper() for k in inventory if k.startswith("PCB")):
        inject("Transistors", "Germanium PNP", 0, "Vintage Option")

    # 3. Standard Enclosure Hardware
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

    # 4. Potentiometer Hardware (Knobs/Seals)
    total_pots = sum(
        d["qty"] for k, d in inventory.items() if k.startswith("Potentiometers")
    )
    if total_pots > 0:
        inject(
            "Hardware/Misc", "Knob", 0, f"Knobs ({total_pots})", qty_override=total_pots
        )
        inject(
            "Hardware/Misc",
            "Dust Seal Cover",
            0,
            f"Pot Seals ({total_pots})",
            qty_override=total_pots,
        )
