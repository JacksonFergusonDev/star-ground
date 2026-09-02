"""Business logic for sourcing, purchasing, and hardware injection.

This module contains the "Nerd Economics" logic, which includes:
- Calculating safe buy quantities (buffers for small parts).
- Generating search terms for suppliers (e.g., Tayda).
- Injecting required hardware (jacks, switches) that isn't on the PCB BOM.
- Identifying missing or residual parts from parsing.
"""

import math
import re
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

import pint

from src.bom_lib import constants
from src.bom_lib.classifier import normalize_value_to_quantity
from src.bom_lib.constants import AUTO_INJECT_SOURCE
from src.bom_lib.enums import ComponentCategory, ComponentSpec
from src.bom_lib.types import Inventory, StatsDict
from src.bom_lib.units import ureg
from src.bom_lib.utils import (
    float_to_search_string,
    parse_value_to_decimal,
)


def _format_alts(alts: list[Any]) -> str:
    """Format an alternatives list into a '💡 TRY: ...' string."""
    txt_parts = [
        f"{item[0]} ({item[1]}{': ' + item[2] if len(item) > 2 else ''})"
        for item in alts
    ]
    return f"💡 TRY: {', '.join(txt_parts)}"


def get_residual_report(stats: StatsDict) -> list[str]:
    """Identifies potential parts hidden in the parser's rejected lines.

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


def get_injection_warnings(inventory: Inventory) -> list[str]:
    """Generates user warnings based on automated hardware injections.

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
            "ℹ️  IC SOCKETS: Added sockets for chips. Optional but recommended."  # noqa: RUF001
        )
    return warnings


def get_spec_type(
    category: ComponentCategory,
    val: str,
    val_qty: pint.Quantity[Any] | Decimal | None = None,
) -> ComponentSpec:
    """Determines the specific capacitor dielectric or material type.

    Used to refine search terms (e.g., distinguishing MLCC from Electrolytic
    based on capacitance).

    Args:
        category: Component category.
        val: Component value string.
        val_qty: Optional pre-computed physical quantity or Decimal.

    Returns:
        A ComponentSpec describing the type (e.g., ComponentSpec.MLCC, ComponentSpec.BOX_FILM),
        or ComponentSpec.NONE if not applicable.
    """
    if category == ComponentCategory.CAPACITORS:
        if val_qty is None:
            val_qty = normalize_value_to_quantity(category, val)
        if val_qty is None:
            return ComponentSpec.NONE

        threshold_1n = Decimal("1e-9") * ureg.farad
        threshold_1u = Decimal("1e-6") * ureg.farad

        # < 1nF -> Ceramic/MLCC
        if val_qty < threshold_1n:
            return ComponentSpec.MLCC

        # 1nF to 1uF -> Film (including exact 1uF)
        if threshold_1n <= val_qty <= threshold_1u:
            return ComponentSpec.BOX_FILM

        # > 1uF -> Electrolytic
        return ComponentSpec.ELECTROLYTIC

    return ComponentSpec.NONE


def generate_search_term(
    category: ComponentCategory,
    val: str,
    spec_type: ComponentSpec = ComponentSpec.NONE,
) -> str:
    """Generates a supplier-optimized search string.

    Targeted primarily at Tayda Electronics' search engine behavior.

    Args:
        category: Component category.
        val: Component value.
        spec_type: Optional material type (from get_spec_type).

    Returns:
        A search string (e.g., "100k ohm Linear potentiometer").
    """
    if category == ComponentCategory.RESISTORS:
        return f"{val} ohm 1/4w metal film"

    if category == ComponentCategory.CAPACITORS:
        # Check if it ends in a shorthand unit (p, n, u) and append 'F'
        if val and val[-1] in "pnu":
            val += "F"

        if spec_type == ComponentSpec.MLCC:
            return f"{val} multilayer"
        if spec_type and spec_type != ComponentSpec.NONE:
            return f"{val} {spec_type.value}"
        return val

    if category == ComponentCategory.POTENTIOMETERS:
        taper = "Linear"  # Default
        val_upper = val.upper()
        is_dual = "DUAL" in val_upper or "STEREO" in val_upper

        for code, name in constants.POT_TAPER_MAP.items():
            if code in val_upper:
                taper = name
                break

        # Clean "B100k" -> "100k"
        taper_chars = "".join(constants.POT_TAPER_MAP.keys())
        clean_raw = re.sub(rf"[{taper_chars}\-\s]", "", val_upper)
        dec = parse_value_to_decimal(clean_raw)

        if dec is not None:
            clean_val = float_to_search_string(float(dec))
        else:
            clean_val = clean_raw if clean_raw else val

        base_term = f"{clean_val} ohm {taper} potentiometer"
        return f"Dual Gang {base_term}" if is_dual else base_term

    if category == ComponentCategory.DIODES:
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
    category: ComponentCategory,
    val: str,
    count: int,
    val_qty: pint.Quantity[Any] | Decimal | None = None,
) -> tuple[int, str]:
    """Calculates the purchase quantity and notes based on 'Nerd Economics'.

    Applies logic to buffer small parts (resistors), enforce exact counts
    for expensive parts (ICs), and suggest substitutions or warnings.

    Args:
        category: Component category.
        val: Component value.
        count: The raw net need (BOM Qty - Stock Qty).
        val_qty: Pre-computed quantity or Decimal value of the component, if available.

    Returns:
        A tuple containing:
            - buy: The integer quantity to purchase.
            - note: A string containing warnings, recommendations, or subs.
    """
    if count <= 0:
        return 0, ""

    buy = count
    note = ""

    # Fallback if val_qty wasn't passed (for backward compatibility or tests)
    if val_qty is None:
        val_qty = normalize_value_to_quantity(category, val)

    # Pre-fetch rules if they exist for this category
    rules = constants.PURCHASING_CONFIG.get(category.value, {})

    if category == ComponentCategory.RESISTORS:
        buffered_qty = count + rules["buffer_add"]
        round_step = rules["round_to"]
        buy = math.ceil(buffered_qty / round_step) * round_step

        note = rules["note"]
        if val_qty is not None and val_qty < rules["suspicious_threshold_low"]:
            note = "⚠️ Suspicious Value (< 1Ω). Verify BOM."

    elif category == ComponentCategory.OPTOELECTRONICS:
        buy = count + 1  # Fragile legs

    elif category == ComponentCategory.CAPACITORS:
        note_parts: list[str] = []
        buffer = rules["standard_buffer"]

        # Bypass caps (100nF) -> Bulk buy
        if val_qty is not None and val_qty == rules["bulk_threshold"]:
            buffer = rules["bulk_buffer"]
            note_parts.append("Power filtering (buy bulk).")
        # Large caps (>= 1uF) -> Low buffer
        elif val_qty is not None and val_qty >= rules["large_threshold"]:
            buffer = rules["large_buffer"]

        buy = count + buffer
        if val_qty is not None and val_qty > rules["suspicious_threshold_high"]:
            note_parts.append("⚠️ Suspicious Value (> 10mF).")

        spec_type = get_spec_type(category, val, val_qty=val_qty)
        if spec_type and spec_type != ComponentSpec.NONE:
            if (
                spec_type == ComponentSpec.BOX_FILM
                and val_qty is not None
                and val_qty == rules["large_threshold"]
            ):
                note_parts.append("Rec: Box Film (Check BOM: Could be Electrolytic)")
            elif spec_type == ComponentSpec.MLCC:
                note_parts.append("Rec: Class 1 Ceramic (C0G / NP0)")
            else:
                note_parts.append(f"Rec: {spec_type.value}")
        note = " | ".join(note_parts)

    elif category == ComponentCategory.DIODES:
        buy = max(10, count + 5)
        # Check substitutions
        if val in constants.DIODE_ALTS:
            alts = constants.DIODE_ALTS[val]
            note = _format_alts(alts)

    elif category == ComponentCategory.TRANSISTORS:
        buy = count + 1
        if "2N5457" in val.upper():
            note = "⚠️ Obsolete part! Check for speciality vendors or consider MMBF5457."
        elif "MMBF" in val.upper():
            note = "SMD Part! Verify PCB pads or buy adapter."

    elif category == ComponentCategory.ICS:
        buy = count
        note = "Socket Recommended"
        clean_ic = re.sub(r"(CP|CN|P|N)$", "", val)
        if clean_ic in constants.IC_ALTS:
            alts = constants.IC_ALTS[clean_ic]
            note += f" | {_format_alts(alts)}"

    elif category == ComponentCategory.CRYSTALS_OSCILLATORS:
        buy = count + 1
        note = "Heat sensitive / Fragile"

    elif category == ComponentCategory.HARDWARE_MISC:
        if "ADAPTER" in val or "SOCKET" in val:
            buy = count + 1
            note = (
                "[AUTO] Verify PCB pads."
                if "ADAPTER" in val
                else "[AUTO] For chip safety."
            )
        else:
            buy = count

    elif category == ComponentCategory.PCB:
        note = "Main Board"

    return buy, note


def get_standard_hardware(inventory: Inventory, pedal_count: int = 1) -> None:
    """Injects standard enclosure hardware into the inventory.

    Adds items like jacks, switches, DC sockets, and wiring that are almost
    never listed on the PCB BOM but are required to build the pedal.

    Args:
        inventory: The inventory dictionary to mutate in-place.
        pedal_count: Number of pedals being built (multiplier for hardware).
    """

    def inject(
        category: ComponentCategory,
        val: str,
        qty_per_pedal: int,
        note: str,
        qty_override: int | None = None,
    ) -> None:
        key = f"{category.value} | {val}"
        total_qty = (
            qty_override if qty_override is not None else (qty_per_pedal * pedal_count)
        )

        inventory[key]["qty"] += total_qty
        inventory[key]["refs"].append("HW")
        inventory[key]["sources"][AUTO_INJECT_SOURCE].append(f"Auto-Inject ({note})")

    # 1. Smart Merges (Add to existing categories)
    inject(ComponentCategory.RESISTORS, "3.3k", 1, "LED CLR")
    inject(ComponentCategory.DIODES, "LED", 1, "Status Light")

    # 2. Germanium Heuristic (Fuzz check)
    if any("FUZZ" in k.upper() for k in inventory if k.startswith("PCB")):
        inject(ComponentCategory.TRANSISTORS, "Germanium PNP", 0, "Vintage Option")

    # 3. Standard Enclosure Hardware
    inject(ComponentCategory.HARDWARE_MISC, "1590B Enclosure", 1, "Verify PCB fit")
    inject(ComponentCategory.HARDWARE_MISC, "3PDT FOOTSWITCH PCB", 1, "Wiring Board")
    inject(ComponentCategory.HARDWARE_MISC, "3PDT STOMP SWITCH", 1, "Blue/Standard")
    inject(ComponentCategory.HARDWARE_MISC, "6.35MM JACK (STEREO)", 1, "Input")
    inject(ComponentCategory.HARDWARE_MISC, "6.35MM JACK (MONO)", 1, "Output")
    inject(ComponentCategory.HARDWARE_MISC, "DC POWER JACK 2.1MM", 1, "Center Negative")
    inject(ComponentCategory.HARDWARE_MISC, "Bezel LED Holder", 1, "3mm Metal")
    inject(ComponentCategory.HARDWARE_MISC, "Rubber Feet (Black)", 4, "Enclosure Feet")
    inject(
        ComponentCategory.HARDWARE_MISC, "AWG 24 Hook-Up Wire", 3, "Approx 1ft/pedal"
    )
    inject(ComponentCategory.HARDWARE_MISC, "9V BATTERY CLIP", 1, "Optional")
    inject(ComponentCategory.HARDWARE_MISC, "Heat Shrink Tubing", 1, "Insulation")

    # 4. Potentiometer Hardware (Knobs/Seals)
    total_pots = sum(
        d["qty"] for k, d in inventory.items() if k.startswith("Potentiometers")
    )
    if total_pots > 0:
        inject(
            ComponentCategory.HARDWARE_MISC,
            "Knob",
            0,
            f"Knobs ({total_pots})",
            qty_override=total_pots,
        )
        inject(
            ComponentCategory.HARDWARE_MISC,
            "Dust Seal Cover",
            0,
            f"Pot Seals ({total_pots})",
            qty_override=total_pots,
        )
