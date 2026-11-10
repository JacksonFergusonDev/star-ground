"""Context-Free Grammar (CFG) parser combinator for SI component values.

Converts engineering notation (e.g., '10k', '4.7u', '100n') and BS 1852
sandwich notation (e.g., '1k5', '4n7', '4K7') into exact `Decimal` base units.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Final

import pyparsing as pp

from star_ground.bom_lib.units import Unit

# Prefix multipliers mapping to Decimal multipliers
SI_MULTIPLIERS: Final[dict[str, Decimal]] = {
    "T": Decimal("1e12"),  # Tera
    "G": Decimal("1e9"),  # Giga
    "M": Decimal("1e6"),  # Mega
    "k": Decimal("1e3"),  # kilo
    "K": Decimal("1e3"),  # kilo (uppercase variant)
    "m": Decimal("1e-3"),  # milli
    "u": Decimal("1e-6"),  # micro (ASCII)
    "U": Decimal("1e-6"),  # micro, upper case ('10UF', '47U')
    "µ": Decimal("1e-6"),  # micro (Unicode)
    "n": Decimal("1e-9"),  # nano
    "N": Decimal("1e-9"),  # nano, upper case ('100NF'); see _SANDWICH_MULTIPLIERS
    "p": Decimal("1e-12"),  # pico
    "P": Decimal("1e-12"),  # pico, upper case ('C3  5P', GuitarPCB Photon Phuzz)
    "R": Decimal("1"),  # Ohms (BS 1852 multiplier)
    "r": Decimal("1"),  # Ohms lowercase variant
}

# --- Atomic Grammar Primitives ---

_DIGIT: Final[pp.ParserElement] = pp.Word(pp.nums)

_sorted_multiplier_keys = sorted(SI_MULTIPLIERS.keys(), key=len, reverse=True)
_MULTIPLIER: Final[pp.ParserElement] = pp.one_of(_sorted_multiplier_keys, exact=True)
# An upper-case N between two numbers is a part number, never a value:
# diodes (1N4148), transistors (2N3904) and optocouplers (4N35) are named so.
_SANDWICH_MULTIPLIERS: Final[pp.ParserElement] = pp.one_of(
    [k for k in _sorted_multiplier_keys if k != "N"], exact=True
)

_UNITS = ["ohm", "ohms", "Ω", "F", "Hz", "H", "V", "W"]
_UNIT: Final[pp.ParserElement] = pp.one_of(_UNITS, caseless=True) | pp.Literal("Ω")
# What each written unit measures, by its lower case ('Ω' is 'ω'); the rest
# (Hz, H, V, W) measure neither.
_UNIT_OF: Final[dict[str, Unit]] = {
    "ohm": Unit.OHM,
    "ohms": Unit.OHM,
    "\u03c9": Unit.OHM,
    "f": Unit.FARAD,
}


# Parse actions take pyparsing's full (string, location, tokens) signature.
# Given fewer, pyparsing finds the arity by calling with all three and
# retrying on TypeError, and that probing isn't thread-safe: two documents
# read at once could leave it calling the action with no arguments, after
# which every value in the process failed to parse.


def _eval_sandwich(_s: str, _loc: int, toks: pp.ParseResults) -> Decimal:
    """Transforms parsed BS 1852 sandwich tokens into a Decimal base value."""
    whole = toks["whole"]
    mult_char = toks["mult"]
    fraction = toks["fraction"]
    val = Decimal(f"{whole}.{fraction}")
    return val * SI_MULTIPLIERS[mult_char]


def _eval_standard(_s: str, _loc: int, toks: pp.ParseResults) -> Decimal:
    """Transforms parsed standard notation tokens into a Decimal base value."""
    num_str = toks["number"]
    val = Decimal(num_str)
    if "mult" in toks:
        val *= SI_MULTIPLIERS[toks["mult"]]
    return val


def build_value_parser() -> pp.ParserElement:
    """Builds and returns the compiled pyparsing expression for SI values.

    Returns:
        A `ParserElement` that parses SI-value strings into `Decimal` objects.
    """
    integer = _DIGIT
    decimal_number = pp.Combine(
        (pp.Word(pp.nums) + pp.Optional(pp.Literal(".") + pp.Word(pp.nums)))
        | (pp.Literal(".") + pp.Word(pp.nums))
    )

    # BS 1852 Sandwich Notation: e.g. "1k5", "4n7", "4K7", "4R7"
    sandwich = (
        integer("whole") + _SANDWICH_MULTIPLIERS("mult") + integer("fraction")
    ).set_parse_action(_eval_sandwich)

    # Standard Notation: e.g. "10k", "4.7u", "100", "0.1", "100kΩ"
    standard = (
        decimal_number("number") + pp.Optional(_MULTIPLIER)("mult")
    ).set_parse_action(_eval_standard)

    # The unit is kept apart from the number so a reading can be checked
    # against the unit the part is measured in.
    return (sandwich | standard) + pp.Optional(_UNIT)("unit") + pp.StringEnd()


_VALUE_PARSER: Final[pp.ParserElement] = build_value_parser()


# A thousands separator: '4,700', '10,000' (never after a lone 0: '0,068').
_THOUSANDS = re.compile(r"^(?!0,)\d{1,3}(?:,\d{3})+(?![\d,])")
# 'MF' and 'MFD' on old schematics are microfarads: no capacitor holds
# megafarads, and 'mfd' was the abbreviation for microfarad. A lower-case
# 'mF' with no D keeps its SI meaning, millifarad.
_FARAD_TAIL = re.compile(r"(?<=[\d.])([MmUuNnPpµ]?)([Ff])([Dd]?)$")


def _farad_tail(m: re.Match[str]) -> str:
    """Writes a farad suffix the grammar reads: '10MFD' -> '10uF'."""
    prefix, fd = m.group(1), m.group(3)
    if prefix == "M" or (prefix == "m" and fd):
        prefix = "u"
    return f"{prefix}{m.group(2)}"


def parse_value_to_decimal(
    val_str: str | None, unit: Unit | None = None
) -> Decimal | None:
    """Parses a component value string into an exact Decimal representation.

    Supports standard engineering notation ('10k', '4.7u', '100', '100NF'),
    BS 1852 sandwich notation ('1k5', '4n7', '4K7'), optional SI unit suffixes
    ('100kΩ', '4.7uF', '10MFD'), and a comma as thousands separator ('4,700')
    or decimal mark ('0,068', '4,7k').

    Args:
        val_str: The raw component value string to parse.
        unit: The unit the part is measured in, if known. A value written with
            another unit is refused: '25V' is no capacitance.

    Returns:
        The normalized Decimal value in base units, or None if parsing fails.
    """
    if not val_str or not isinstance(val_str, str):
        return None

    # Fonts set the micro prefix as Greek mu too ('4μ7', Aion FX legacy).
    cleaned_str = val_str.strip().replace("\u03bc", "\u00b5")
    # 'R31  47 ohm' (GuitarPCB Mini-Me): a unit spelled out after a space.
    cleaned_str = re.sub(
        r"(?<=\d)\s+(?=(?:ohms?|\u03a9)$)", "", cleaned_str, flags=re.I
    )
    if _THOUSANDS.match(cleaned_str):
        cleaned_str = cleaned_str.replace(",", "")
    # A comma left between digits is a decimal mark ('0,068 µF', Excel in
    # countries that write one).
    cleaned_str = re.sub(r"(?<=\d),(?=\d)", ".", cleaned_str)
    cleaned_str = _FARAD_TAIL.sub(_farad_tail, cleaned_str)
    if not cleaned_str:
        return None

    try:
        results = _VALUE_PARSER.parse_string(cleaned_str, parse_all=True)
    except pp.ParseException, InvalidOperation:
        return None
    res = results[0]
    if not isinstance(res, Decimal):
        return None
    written = str(results["unit"]) if "unit" in results else ""
    if unit is not None and written and _UNIT_OF.get(written.lower()) != unit:
        return None
    return res
