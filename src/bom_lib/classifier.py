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
# '(3362P Trim)', 'Trim (3362P)', '(3362P)', '5k trimmer (3386)': package notes
# (Bourns trimmer series), not part of the value.
_TRIM_NOTE = re.compile(
    r"\([^()]*\b(?:33(?:62|86)P?|3296[A-Z]?|TRIM(?:MER|POT)?)\b[^()]*\)"
    r"|\b3362P\b|\bTRIM(?:MER|POT)?\b",
    re.IGNORECASE,
)
_RESISTANCE = re.compile(r"\b\d+(?:\.\d+)?[KkMR]\d*\b")
_MILLIAMPS = re.compile(r"\dmA\b")
_REPEAT_SUFFIX = re.compile(r"\s+\(\d+\)$")
_LED_VALUE = re.compile(r"\bLED\b")
_NUMBERED_LABEL = re.compile(r"[A-Z]{2,5}\d{1,3}")
_SLASH_RATING = re.compile(r"(?<=[\dpnuµF])/(\d+(?:\.\d+)?)\s*V\b", re.IGNORECASE)
_OHMS = re.compile(r"(?<![\w.])\d+(?:\.\d+)?R\d*(?![\w.])")
# Vactrols: VTL5C4 (Xvive/PerkinElmer), NSL-32 (Silonex).
_VACTROL = re.compile(r"VTL\d|NSL-?\d")
# A DIP switch, written with a dash ('DIP-5'), unlike socket sizes ('DIP8').
DIP_SWITCH = re.compile(r"\bDIP-\d+\b|\bDIP[- ]?\d+\b.*\bSWITCH|\bDIP SWITCH")
_JACK_VALUE = re.compile(r"\b(?:TRS|JACK)\b")
_POT_VALUE = re.compile(
    r"(?<![\w.])(?:[ABCWG]\d+(?:\.\d+)?[KM][ABC]?|\d+(?:\.\d+)?[KM][ABCWG]?)(?![\w.])"
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


_OHM_WORD = re.compile(r"^(\d+(?:\.\d+)?[kKM]?)\s*ohms?\b", re.IGNORECASE)


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

    if category == ComponentCategory.CAPACITORS:
        # '330uF/35V' (Aion FX) gives the voltage rating with the value.
        clean_val = _SLASH_RATING.sub(r" \1V", clean_val)

    # Only normalize Passives (Resistors/Caps)
    if category in (ComponentCategory.RESISTORS, ComponentCategory.CAPACITORS):
        # Exception: Don't normalize physical dimensions (e.g., "5mm LDR")
        if "mm" in clean_val.lower():
            return clean_val

        # 'R31  47 ohm' (GuitarPCB Mini-Me): the unit spelled out.
        clean_val = _OHM_WORD.sub(r"\1", clean_val)
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


_FARADS = re.compile(r"\d+(?:\.\d+)?\s*[PNUµ]F")


def _named_control_category(
    name: str, val_up: str, val: str
) -> ComponentCategory | None:
    """Category of a named control (VOLUME, SHIFT, OSC-SW), decided by its value."""
    if constants.SWITCH_VALUE_PATTERN.search(val_up):
        return ComponentCategory.SWITCHES
    # '250mA' is a current rating, not a 250M audio-taper pot.
    if _POT_VALUE.search(val_up) and not _MILLIAMPS.search(val):
        return ComponentCategory.POTENTIOMETERS
    # An ohms trimmer: Frost Drive's 'FREQ  500R TRIM'.
    if _OHMS.search(val_up) and _TRIM_NOTE.search(val):
        return ComponentCategory.POTENTIOMETERS
    # A vactrol on an unfamiliar designator: CDXL's 'OC1  VTL5C4/2'.
    if _VACTROL.match(val_up):
        return ComponentCategory.OPTOELECTRONICS
    # Muffin Factory: 'DIP1  DIP-5 Dip Switch'.
    if DIP_SWITCH.search(val_up):
        return ComponentCategory.SWITCHES
    # Muzzle: 'SIDECHAIN  1/4" Switching TRS'.
    if _JACK_VALUE.search(val_up):
        return ComponentCategory.HARDWARE_MISC
    if name in constants.SWITCH_LABELS and any(
        x in val_up for x in ("ON", "SW", "SP", "DP")
    ):
        return ComponentCategory.SWITCHES
    if name in constants.POT_LABELS:
        return ComponentCategory.POTENTIOMETERS
    # Voodoo Redux: 'EFL1 - EFL3  Status Led' (effect-on LEDs). Only a
    # numbered label: drill-template boxes read as 'LED FOOTSWITCH Drive'.
    if _NUMBERED_LABEL.fullmatch(name) and _LED_VALUE.search(val_up):
        return ComponentCategory.DIODES
    # Five Cats Custom Ranger: 'CR1  2.2nF', a rotary switch's capacitors.
    # Only a value in farads says capacitor; '10K' could be a pot.
    if _NUMBERED_LABEL.fullmatch(name) and _FARADS.fullmatch(val_up):
        return ComponentCategory.CAPACITORS
    return None


_BARE_RESISTANCE = re.compile(r"\d+(?:\.\d+)?[kKM]\d*|\d+[kKM]?\d*R\d*")


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
        category_hint: Category named by a TYPE column; for a label that is not
            a designator it wins over the guess from the value (RECV /
            'Stereo Jack', LEDR / 'Metal film resistor').

    Returns:
        CategorizationResult with standardized category, normalized value,
        and optional secondary injected part key; or None if invalid or ignored.
    """
    ref_up = ref.upper().strip()
    val_clean = val.strip()  # Keep original case for display
    val_up = val_clean.upper()  # Use this for internal logic

    # 'R1 (2)' is R1's second listing (another board, or a repeated label).
    designator = parse_designator(_REPEAT_SUFFIX.sub("", ref.strip()))
    if designator is not None:
        # L is an inductor, except where a document numbers its LEDs L1-L4.
        category = designator.category or (
            ComponentCategory.DIODES
            if "LED" in val_up
            else ComponentCategory.HARDWARE_MISC
        )
        if category == ComponentCategory.ICS and _VACTROL.match(val_up):
            # Constrictor: 'U1  VTL5C10' is an optoisolator, not a chip.
            category = ComponentCategory.OPTOELECTRONICS
        if (
            category
            in (
                ComponentCategory.RESISTORS,
                ComponentCategory.HARDWARE_MISC,
                ComponentCategory.DIODES,
            )
            and _TRIM_NOTE.search(val_clean)
            and _RESISTANCE.search(val_clean)
        ):
            # CDXL Reissue lists trimmers R20/R23 under 'TRIM POTS (3362P Type)';
            # GuitarPCB numbers trimmers 'T1 - T2  10k Trim' and labels an LED
            # brightness trimmer 'LED  1k Trimpot' (Voodoo Redux).
            category = ComponentCategory.POTENTIOMETERS
        elif designator.prefix == "T" and _BARE_RESISTANCE.fullmatch(val_clean):
            # A transformer is no bare resistance: GuitarPCB numbers trimmers
            # 'T1  5k' (ColorTone Bass Fuzz).
            category = ComponentCategory.POTENTIOMETERS
    else:
        # A TYPE column states the category outright; the value is only a
        # guess (Aion FX: 'LEDR | 10k | Metal film resistor' is no pot).
        named = category_hint or _named_control_category(ref_up, val_up, val_clean)
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
        skip_injection_keywords = [
            "REGULATOR",
            "L78L",
            "MODULE",
            "BTDR",
            "REVERB",
            "ESP32",
        ]
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
