"""Component classification and heuristic categorization logic.

This module determines what a component *is* (e.g., Resistor, Potentiometer, IC)
based on its reference designator (R1, U1) and value. It also normalizes
values to ensure consistent matching between BOMs and Inventory.
"""

import re
from decimal import Decimal
from typing import Any

import pint

from src.bom_lib import constants
from src.bom_lib.designators import parse_designator
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.types import CategorizationResult, ComponentKey
from src.bom_lib.units import ureg
from src.bom_lib.utils import (
    float_to_search_string,
    parse_value_to_decimal,
)

_SIZED_LED = re.compile(r"\d+(?:\.\d+)?mm(?:\s+[A-Za-z]+)?", re.IGNORECASE)
# A resistance, with or without a pot taper: B100K, 100KA, 50K, B10K DUAL, Dual B10K.
# '(3362P Trim)', 'Trim (3362P)', '(3362P)': package notes, not part of the value.
_TRIM_NOTE = re.compile(
    r"\([^()]*\b(?:3362P|TRIM(?:MER)?)\b[^()]*\)|\b3362P\b|\bTRIM(?:MER)?\b",
    re.IGNORECASE,
)
_MILLIAMPS = re.compile(r"\dmA\b")
_POT_VALUE = re.compile(
    r"(?<![\w.])(?:[ABCWG]\d+(?:\.\d+)?[KM]|\d+(?:\.\d+)?[KM][ABCWG]?)(?![\w.])"
)


def normalize_value_to_quantity(
    category: ComponentCategory, val_raw: str
) -> pint.Quantity[Any] | Decimal | None:
    """Attaches the correct physical unit to the parsed value based on category.

    Args:
        category: The determined component category (e.g., ComponentCategory.RESISTORS).
        val_raw: The raw value string from the BOM (e.g., "10k", "100n").

    Returns:
        A pint.Quantity with ureg.ohm for Resistors, ureg.farad for Capacitors,
        a Decimal for other categories with numeric values, or None if parsing fails.
    """
    clean_val = val_raw.strip()
    if "mm" in clean_val.lower():
        return None

    # Qualifiers after the number ('1u MLCC', '10u (25V or higher)') do not
    # change the nominal value.
    head = clean_val.split()[0] if clean_val else clean_val
    dec = parse_value_to_decimal(head)
    if dec is None:
        return None

    if category == ComponentCategory.RESISTORS:
        return ureg.Quantity(dec, ureg.ohm)
    if category == ComponentCategory.CAPACITORS:
        return ureg.Quantity(dec, ureg.farad)
    return dec


def normalize_value_by_category(category: ComponentCategory, val_raw: str) -> str:
    """Standardizes component values for consistent string matching.

    For passives (Resistors/Caps), this converts raw strings like "10,000" or
    "10K" into a canonical format "10k" using the utility parser. Qualifiers
    after the number are kept ('1uF MLCC' -> '1u MLCC').

    Args:
        category: The determined component category.
        val_raw: The raw value string from the BOM (e.g., "100n").

    Returns:
        A normalized string (e.g., "100n") or the original string if
        normalization isn't applicable.
    """
    clean_val = val_raw.strip()

    # Only normalize Passives (Resistors/Caps)
    if category in (ComponentCategory.RESISTORS, ComponentCategory.CAPACITORS):
        # Exception: Don't normalize physical dimensions (e.g., "5mm LDR")
        if "mm" in clean_val.lower():
            return clean_val

        head, _, rest = clean_val.partition(" ")
        dec = parse_value_to_decimal(head)
        if dec is not None:
            clean_val = " ".join(
                p for p in (float_to_search_string(float(dec)), rest.strip()) if p
            )

    # A diode specified only by size and color ('5mm Yellow') is an LED.
    if (
        category == ComponentCategory.DIODES
        and _SIZED_LED.fullmatch(clean_val)
        and "LED" not in clean_val.upper()
    ):
        clean_val = f"{clean_val} LED"

    # A trimmer's package note is not part of its value ('10K Trim (3362P)').
    if category == ComponentCategory.POTENTIOMETERS:
        clean_val = " ".join(_TRIM_NOTE.sub(" ", clean_val).split()) or clean_val

    return clean_val


def _named_control_category(
    name: str, val_up: str, val: str
) -> ComponentCategory | None:
    """Category of a named control (VOLUME, SHIFT, OSC-SW), decided by its value."""
    if constants.SWITCH_VALUE_PATTERN.search(val_up):
        return ComponentCategory.SWITCHES
    # '250mA' is a current rating, not a 250M audio-taper pot.
    if _POT_VALUE.search(val_up) and not _MILLIAMPS.search(val):
        return ComponentCategory.POTENTIOMETERS
    if name in constants.SWITCH_LABELS and any(
        x in val_up for x in ("ON", "SW", "SP", "DP")
    ):
        return ComponentCategory.SWITCHES
    if name in constants.POT_LABELS:
        return ComponentCategory.POTENTIOMETERS
    return None


def categorize_part(
    ref: str, val: str, category_hint: ComponentCategory | None = None
) -> CategorizationResult | None:
    """Classifies a component based on its Reference Designator and Value.

    A recognized designator (R1, IC_A, CLR1, JACK_IN; see `designators.py`)
    states its own category. A named control (VOLUME, SHIFT, OSC-SW, INT_BIAS)
    is classified by its value: a switch spec makes it a switch, a resistance
    (with or without a taper) makes it a potentiometer or trimmer.

    Args:
        ref: The reference designator (e.g., "R1", "VOLUME", "IC1").
        val: The component value (e.g., "10k", "TL072").
        category_hint: Category suggested by a TYPE column, used only when
            neither the designator nor the value decides (RECV / 'Stereo Jack').

    Returns:
        CategorizationResult with standardized category, normalized value,
        and optional secondary injected part key; or None if invalid or ignored.
    """
    ref_up = ref.upper().strip()
    val_clean = val.strip()  # Keep original case for display
    val_up = val_clean.upper()  # Use this for internal logic

    designator = parse_designator(ref.strip())
    if designator is not None:
        # L is an inductor, except where a document numbers its LEDs L1-L4.
        category = designator.category or (
            ComponentCategory.DIODES
            if "LED" in val_up
            else ComponentCategory.HARDWARE_MISC
        )
    else:
        named = _named_control_category(ref_up, val_up, val_clean) or category_hint
        if named is None:
            return None
        category = named

    injection: ComponentKey | None = None

    if category == ComponentCategory.OPTOELECTRONICS:
        return CategorizationResult(
            category=category, clean_value=val_clean, injected_key=None
        )

    # ICs & Socket Injection
    if category == ComponentCategory.ICS:
        # Don't inject sockets for things that aren't DIP chips
        # (e.g., Regulators, Reverb Bricks)
        skip_injection_keywords = ["REGULATOR", "L78L", "MODULE", "BTDR", "REVERB"]
        if not any(k in val_up for k in skip_injection_keywords):
            injection = ComponentKey(
                ComponentCategory.HARDWARE_MISC, "DIP SOCKET (Check Size)"
            )

    # Final Normalization
    val_clean = normalize_value_by_category(category, val_clean)

    return CategorizationResult(
        category=category,
        clean_value=val_clean,
        injected_key=injection,
    )
