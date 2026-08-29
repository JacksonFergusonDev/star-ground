"""Unit tests for the isolated Context-Free Grammar (CFG) SI-value parser."""

from decimal import Decimal
from typing import cast

import pytest

from src.bom_lib.grammar import (
    DIGIT,
    MULTIPLIER,
    UNIT,
    build_value_parser,
    parse_si_value,
    parse_value_to_decimal,
)


@pytest.mark.parametrize(
    ("val_str", "expected"),
    [
        # Standard notation
        ("10k", Decimal("10000")),
        ("4.7u", Decimal("4.7e-6")),
        ("100n", Decimal("1e-7")),
        ("2.2M", Decimal("2.2e6")),
        ("100p", Decimal("100e-12")),
        ("10m", Decimal("0.01")),
        ("1G", Decimal("1e9")),
        ("4.7µ", Decimal("4.7e-6")),
        # Sandwich notation (BS 1852)
        ("1k5", Decimal("1500")),
        ("4n7", Decimal("4.7e-9")),
        ("2M2", Decimal("2200000")),
        ("4u7", Decimal("4.7e-6")),
        ("2p2", Decimal("2.2e-12")),
        ("4K7", Decimal("4700")),
        ("4R7", Decimal("4.7")),
        ("0R1", Decimal("0.1")),
        # Bare numbers
        ("100", Decimal("100")),
        ("0.1", Decimal("0.1")),
        ("0", Decimal("0")),
        ("470", Decimal("470")),
        # With units
        ("100kΩ", Decimal("100000")),
        ("4.7uF", Decimal("4.7e-6")),
        ("100pF", Decimal("100e-12")),
        ("10mH", Decimal("0.01")),
        ("100ohm", Decimal("100")),
        ("100ohms", Decimal("100")),
    ],
)
def test_parse_value_to_decimal_valid(val_str: str, expected: Decimal) -> None:
    """Verifies standard, sandwich, bare numbers, and unit notation parsing."""
    result = parse_value_to_decimal(val_str)
    assert result is not None
    assert result == expected

    # Also check alias parse_si_value
    assert parse_si_value(val_str) == expected


@pytest.mark.parametrize(
    "val_str",
    [
        "",
        "   ",
        "FOOBAR",
        "R1",
        "hello",
        "10k5extra",
        "xyz10",
    ],
)
def test_parse_value_to_decimal_none_returns(val_str: str) -> None:
    """Verifies that invalid or empty strings return None."""
    assert parse_value_to_decimal(val_str) is None
    assert parse_si_value(val_str) is None


def test_parse_value_to_decimal_falsy_none() -> None:
    """Verifies that None input safely returns None."""
    assert parse_value_to_decimal(cast(str, None)) is None
    assert parse_si_value(cast(str, None)) is None


def test_grammar_primitives() -> None:
    """Verifies standalone parsing of atomic grammar primitives."""
    # DIGIT
    assert DIGIT.parse_string("123", parse_all=True)[0] == "123"

    # MULTIPLIER
    assert MULTIPLIER.parse_string("k", parse_all=True)[0] == "k"
    assert MULTIPLIER.parse_string("µ", parse_all=True)[0] == "µ"
    assert MULTIPLIER.parse_string("M", parse_all=True)[0] == "M"

    # UNIT
    assert UNIT.parse_string("Ω", parse_all=True)[0] == "Ω"
    assert UNIT.parse_string("F", parse_all=True)[0] == "F"
    assert UNIT.parse_string("ohm", parse_all=True)[0] == "ohm"

    # build_value_parser returns valid parser
    parser = build_value_parser()
    assert parser is not None
