"""Context-Free Grammar (CFG) parser combinator for SI component values.

Converts engineering notation (e.g., '10k', '4.7u', '100n') and BS 1852
sandwich notation (e.g., '1k5', '4n7', '4K7') into exact `Decimal` base units.
"""

from decimal import Decimal, InvalidOperation
from typing import Final

import pyparsing as pp

# Prefix multipliers mapping to Decimal multipliers
SI_MULTIPLIERS: Final[dict[str, Decimal]] = {
    "T": Decimal("1e12"),  # Tera
    "G": Decimal("1e9"),  # Giga
    "M": Decimal("1e6"),  # Mega
    "k": Decimal("1e3"),  # kilo
    "K": Decimal("1e3"),  # kilo (uppercase variant)
    "m": Decimal("1e-3"),  # milli
    "u": Decimal("1e-6"),  # micro (ASCII)
    "µ": Decimal("1e-6"),  # micro (Unicode)
    "n": Decimal("1e-9"),  # nano
    "p": Decimal("1e-12"),  # pico
    "R": Decimal("1"),  # Ohms (BS 1852 multiplier)
    "r": Decimal("1"),  # Ohms lowercase variant
}

# --- Atomic Grammar Primitives ---

DIGIT: Final[pp.ParserElement] = pp.Word(pp.nums)

_sorted_multiplier_keys = sorted(SI_MULTIPLIERS.keys(), key=len, reverse=True)
MULTIPLIER: Final[pp.ParserElement] = pp.one_of(_sorted_multiplier_keys, exact=True)

_UNITS = ["ohm", "ohms", "Ω", "F", "Hz", "H", "V", "W"]
UNIT: Final[pp.ParserElement] = pp.one_of(_UNITS, caseless=True) | pp.Literal("Ω")


def _eval_sandwich(toks: pp.ParseResults) -> Decimal:
    """Transforms parsed BS 1852 sandwich tokens into a Decimal base value."""
    whole = toks["whole"]
    mult_char = toks["mult"]
    fraction = toks["fraction"]
    val = Decimal(f"{whole}.{fraction}")
    return val * SI_MULTIPLIERS[mult_char]


def _eval_standard(toks: pp.ParseResults) -> Decimal:
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
    integer = DIGIT
    decimal_number = pp.Combine(
        (pp.Word(pp.nums) + pp.Optional(pp.Literal(".") + pp.Word(pp.nums)))
        | (pp.Literal(".") + pp.Word(pp.nums))
    )

    # BS 1852 Sandwich Notation: e.g. "1k5", "4n7", "4K7", "4R7"
    sandwich = (
        integer("whole") + MULTIPLIER("mult") + integer("fraction") + pp.Optional(UNIT)
    ).set_parse_action(_eval_sandwich)

    # Standard Notation: e.g. "10k", "4.7u", "100", "0.1", "100kΩ"
    standard = (
        decimal_number("number") + pp.Optional(MULTIPLIER)("mult") + pp.Optional(UNIT)
    ).set_parse_action(_eval_standard)

    return (sandwich | standard) + pp.StringEnd()


_VALUE_PARSER: Final[pp.ParserElement] = build_value_parser()


def parse_value_to_decimal(val_str: str | None) -> Decimal | None:
    """Parses a component value string into an exact Decimal representation.

    Supports standard engineering notation ('10k', '4.7u', '100'), BS 1852
    sandwich notation ('1k5', '4n7', '4K7'), and optional SI unit suffixes
    ('100kΩ', '4.7uF').

    Args:
        val_str: The raw component value string to parse.

    Returns:
        The normalized Decimal value in base units, or None if parsing fails.
    """
    if not val_str or not isinstance(val_str, str):
        return None

    cleaned_str = val_str.strip()
    if not cleaned_str:
        return None

    try:
        results = _VALUE_PARSER.parse_string(cleaned_str, parse_all=True)
        res = results[0]
        if isinstance(res, Decimal):
            return res
        return None
    except pp.ParseException, InvalidOperation:
        return None


# Alias for explicit grammar-specific naming
parse_si_value = parse_value_to_decimal
