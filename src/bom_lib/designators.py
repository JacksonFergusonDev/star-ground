"""Reference designator grammar: what a designator looks like and what it implies.

A designator is a known prefix followed by a number with an optional letter
suffix (R1, IC100, R1A, D10a), or by an underscore and a name (IC_A, JACK_IN,
SW_C). A few are bare (LED, CLR, RLED, TRIM). Anything else that labels a part
(VOLUME, SHIFT, OSC-SW, INT_BIAS) is a *named control*, whose category comes
from its value instead.

The classifier and the layout reader share this module so the two can never
disagree about what counts as a designator. The self-checks in
``verification.py`` deliberately keep their own, independent rules.
"""

import re
from dataclasses import dataclass

from src.bom_lib.enums import ComponentCategory

_CAT = ComponentCategory

# Longest prefixes first so CLR1 is a resistor (not C), TRIM1 a trimmer (not TR),
# and LDR1 an LDR (not L). A None category is resolved from the value.
_PREFIXES: tuple[tuple[str, ComponentCategory | None], ...] = (
    ("JACK", _CAT.HARDWARE_MISC),
    ("TRIM", _CAT.POTENTIOMETERS),
    ("XFMR", _CAT.TRANSFORMERS),  # transformers (Aion FX: 'XFM1  42TM018')
    ("XFM", _CAT.TRANSFORMERS),
    ("RLED", _CAT.RESISTORS),
    ("LEDR", _CAT.RESISTORS),  # Aion FX: LED current-limiting resistor
    ("RPD", _CAT.RESISTORS),  # Aion FX: input pulldown resistor
    ("REG", _CAT.ICS),  # voltage regulators (GuitarPCB: 'REG  L78L05')
    ("RG", _CAT.ICS),  # Aion FX legacy Blueshift: 'RG1  78L06'
    ("RX", _CAT.RESISTORS),  # Aion FX: optional-mod resistors RX1, RX2
    ("CX", _CAT.CAPACITORS),
    ("DX", _CAT.DIODES),  # Aion FX: alternate clipping diodes DX1-DX4
    ("QX", _CAT.TRANSISTORS),  # Aion FX legacy L5 Preamp: 'QX1, QX2  2N5088'
    ("LDR", _CAT.OPTOELECTRONICS),
    ("LED", _CAT.DIODES),
    ("CLR", _CAT.RESISTORS),
    ("CONN", _CAT.HARDWARE_MISC),  # connectors (GuitarPCB Test Rig: 'CONN1')
    ("POT", _CAT.POTENTIOMETERS),
    ("PNP", _CAT.TRANSISTORS),  # Twin Face: 'PNP1  AC128'
    ("NPN", _CAT.TRANSISTORS),
    ("SW", _CAT.SWITCHES),
    ("IC", _CAT.ICS),
    ("OP", _CAT.ICS),
    ("TR", _CAT.POTENTIOMETERS),
    ("VR", _CAT.POTENTIOMETERS),
    ("RV", _CAT.POTENTIOMETERS),  # KiCad variable resistor (Sheepylove: 'RV1  50k')
    ("R", _CAT.RESISTORS),
    ("C", _CAT.CAPACITORS),
    ("D", _CAT.DIODES),
    ("Q", _CAT.TRANSISTORS),
    ("Z", _CAT.DIODES),  # zener diodes (Aion FX: 'Z1  1N4742A')
    ("U", _CAT.ICS),
    ("X", _CAT.CRYSTALS_OSCILLATORS),
    ("Y", _CAT.CRYSTALS_OSCILLATORS),
    ("J", _CAT.HARDWARE_MISC),
    ("T", _CAT.TRANSFORMERS),
    ("V", _CAT.HARDWARE_MISC),  # vacuum tubes (SuperNuva: 'V1  Korg Nutube')
    ("K", _CAT.RELAYS),
    ("L", None),  # inductors, but some documents number LEDs L1-L4
    ("P", _CAT.POTENTIOMETERS),  # GuitarPCB: 'P1  GAIN 100k Lin'
)

# Designators printed without a number or name suffix.
# 'RX' is an optional resistor (GuitarPCB Mini-Me 'Rx  1M'), a lone 'IC' the
# only chip besides IC1 (Tube Screaming Preamp), 'REG' the voltage regulator.
_BARE = frozenset(
    {"LED", "CLR", "RLED", "LEDR", "RPD", "TRIM", "RX", "CX", "IC", "REG"}
)
# A lettered resistor (GuitarPCB D'Lay's 'R-A') and a switch numbered a space
# apart ('SW 1', GuitarPCB EA Tremelo).
# 'DA' / 'DB' are two LEDs in place of one bi-colour D1 (GuitarPCB Easy Order).
_LETTERED = re.compile(r"(R)-[A-Z]|(SW) \d{1,2}|(D)[AB]")
# Tonepad letters the parts that differ between builds in lower case: 'Ra - 100',
# 'Cb - 1 µF'. Upper case would take words ('RE', 'CD').
_LOWER_LETTERED = re.compile(r"([RC])[a-e]")

# A channel suffix letter (R1A/R1B, D10a-D10d). K, M and R are excluded so a
# C-taper pot value (C10K, C1M) is never mistaken for capacitor C10.
_SUFFIX = re.compile(r"\d{1,3}[A-Ha-h]?|_[A-Za-z0-9]+")

# Board designators that can never be a part number in a value column. Part
# numbers share the letters-then-digits shape (TL072, BS170, J201, T1308), so
# only these short prefixes are trusted to start a new listing there.
BOARD_REF = re.compile(
    r"(?:(?:R|C|D|Q|IC|U|SW|LED|LDR|X|Y|VR|TRIM|TR|CLR)\d{1,3}[A-Ha-h]?"
    # A switch symbol's name is a value: 'CLIP1 | SW_SPDT' (Five Cats).
    r"|(?:IC|LED|JACK|SW)_(?!(?:[SD]P[SD]T|[34]PDT)$)[A-Za-z0-9]+)"
)


@dataclass(frozen=True, slots=True)
class Designator:
    """A recognized reference designator.

    Attributes:
        prefix: The matched prefix (e.g. 'R', 'IC', 'JACK').
        category: The category the prefix implies, or None when only the
            value can decide (L: inductor or LED).
    """

    prefix: str
    category: ComponentCategory | None


def parse_designator(ref: str) -> Designator | None:
    """Recognizes a reference designator.

    Args:
        ref: The candidate designator, e.g. 'R1A', 'IC_A', 'LED', 'VOLUME'.

    Returns:
        The Designator, or None for named controls and non-designators.
    """
    token = ref.strip()
    upper = token.upper()
    if upper in _BARE:
        return Designator(upper, dict(_PREFIXES)[upper])
    if upper[:-1] in _BARE and upper[-1] in "ABCDEFGH":  # LEDA / LEDB channels
        return Designator(upper[:-1], dict(_PREFIXES)[upper[:-1]])
    lettered = _LETTERED.fullmatch(upper) or _LOWER_LETTERED.fullmatch(token)
    if lettered:
        prefix = next(g for g in lettered.groups() if g)
        return Designator(prefix, dict(_PREFIXES)[prefix])
    for prefix, category in _PREFIXES:
        if upper.startswith(prefix) and _SUFFIX.fullmatch(token[len(prefix) :]):
            return Designator(prefix, category)
    return None


def is_designator(ref: str) -> bool:
    """Whether a token is a reference designator (as opposed to a control name)."""
    return parse_designator(ref) is not None
