"""Reads a parts table's TYPE and NOTES columns into the part's value.

A ruled table may print '1n' under VALUE and 'Film' under TYPE; the
builder buys a '1n Film' capacitor.
"""

import re

from star_ground.bom_lib import constants
from star_ground.bom_lib.designators import parse_designator
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.utils import parse_value_to_decimal

_PART_NUMBER = re.compile(r"\b\d?[A-Z]{1,4}\d{2,5}[A-Z]{0,2}\b")


POLE_THROW = re.compile(r"\b(?:[SD34]P[DS]T|\dP\dT)\b")


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
    ("relay", ComponentCategory.RELAYS),
    ("transformer", ComponentCategory.TRANSFORMERS),
    ("inductor", ComponentCategory.INDUCTORS),
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


# '25V', '25 VDC', '16+V' (GuitarPCB Fuzzy Bee: '16 volts or more').
_VOLTAGE = re.compile(r"\b(\d+(?:\.\d+)?)\s*\+?\s*V(?:DC)?\b", re.IGNORECASE)


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


def drop_restated_value(value: str) -> str:
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
    if not any(ch.isdigit() for ch in value) and not POLE_THROW.search(value.upper()):
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
    if switch_value and not POLE_THROW.search(value.upper()):
        pole = POLE_THROW.search(type_text.upper())
        if pole:
            return f"{pole.group(0)} {value}"
    if POLE_THROW.fullmatch(value.upper()) and "ROTARY" in detail:
        # '2P4T' / '16mm Mini Rotary switch, 2P4T' (Hwut Fuzz).
        return f"{value} {'Mini Rotary' if 'MINI ROTARY' in detail else 'Rotary'}"
    if POLE_THROW.fullmatch(value.upper()):
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
        # A note naming another part describes that part: 'Can omit and use
        # 4.7uF tantalum for C6' (Aion FX Dryad) on the 1uF film cap C5.
        # 'Original amp uses 2.7uF tantalum, but 2.2uF film will work the same'
        # (Aion FX L4): the original's part, not this one.
        other = _OTHER_PART.search(notes) or _ORIGINAL_PART.search(notes)
        construction = type_text.upper() if other else detail
        for keyword, name in _DIELECTRICS:
            # 'Box/MLCC Capacitor' (Five Cats) leaves the choice open.
            either = re.search(rf"/\s*{keyword}|{keyword}\s*/", construction)
            if keyword in construction and keyword not in value.upper() and not either:
                return f"{value} {name}"
    return value


# A designator named in a NOTES cell: 'use 4.7uF tantalum for C6'.
_OTHER_PART = re.compile(r"\b(?:for|in|with) [A-Z]{1,3}\d{1,3}\b")


_ORIGINAL_PART = re.compile(r"\boriginal\b", re.IGNORECASE)
