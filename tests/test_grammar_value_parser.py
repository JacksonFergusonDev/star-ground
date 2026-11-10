"""Unit tests for the isolated Context-Free Grammar (CFG) SI-value parser."""

import threading
from decimal import Decimal
from typing import cast

import pyparsing as pp
import pytest

from star_ground.bom_lib.grammar import parse_value_to_decimal
from star_ground.bom_lib.grammar.value_parser import build_value_parser
from star_ground.bom_lib.units import Unit


@pytest.mark.parametrize(
    ("val_str", "expected"),
    [
        # Standard notation
        ("10k", Decimal("10000")),
        ("4.7u", Decimal("4.7e-6")),
        ("100n", Decimal("1e-7")),
        ("2.2M", Decimal("2.2e6")),
        ("100p", Decimal("100e-12")),
        ("5P", Decimal("5e-12")),  # GuitarPCB Photon Phuzz
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


def test_parse_value_to_decimal_falsy_none() -> None:
    """Verifies that None input safely returns None."""
    assert parse_value_to_decimal(cast(str, None)) is None


def test_greek_mu_is_micro() -> None:
    # Aion FX legacy fonts set '4μ7' with the Greek letter.
    from decimal import Decimal

    from star_ground.bom_lib.utils import parse_value_to_decimal

    assert parse_value_to_decimal("4\u03bc7") == Decimal("0.0000047")
    assert parse_value_to_decimal("10\u03bcF") == Decimal("0.000010")


def test_spelled_out_ohms() -> None:
    from decimal import Decimal

    from star_ground.bom_lib.classifier import categorize_part
    from star_ground.bom_lib.utils import parse_value_to_decimal

    assert parse_value_to_decimal("47 ohm") == Decimal("47")
    result = categorize_part("R31", "47 ohm")
    assert result is not None
    assert result.clean_value == "47"


def test_a_numbered_t_with_a_resistance_is_a_trimmer() -> None:
    from star_ground.bom_lib.classifier import categorize_part
    from star_ground.bom_lib.enums import ComponentCategory

    trimmer = categorize_part("T1", "5k")
    transformer = categorize_part("T1", "42TM002")
    assert trimmer is not None
    assert trimmer.category == ComponentCategory.POTENTIOMETERS
    assert transformer is not None
    assert transformer.category == ComponentCategory.TRANSFORMERS


def _parse_together(
    parser: pp.ParserElement, barrier: threading.Barrier, results: list[object]
) -> None:
    barrier.wait()
    try:
        results.append(parser.parse_string("4k7", parse_all=True)[0])
        results.append(parser.parse_string("10k", parse_all=True)[0])
    except TypeError as e:
        results.append(e)


def test_a_fresh_parser_is_safe_to_share_between_threads() -> None:
    """Documents are read on several threads at once (the web API).

    pyparsing found a parse action's arity by retrying it on TypeError, which
    raced: concurrent first calls left it calling the action with no
    arguments, and every value after that failed to parse.
    """
    for _ in range(50):
        parser = build_value_parser()
        barrier = threading.Barrier(8)
        results: list[object] = []
        threads = [
            threading.Thread(target=_parse_together, args=(parser, barrier, results))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert set(results) == {Decimal(4700), Decimal(10000)}


@pytest.mark.parametrize(
    ("val_str", "expected"),
    [
        # Upper-case nano and micro, as some schematics print them.
        ("100NF", Decimal("100e-9")),
        ("100N", Decimal("100e-9")),
        ("10UF", Decimal("10e-6")),
        ("47U", Decimal("47e-6")),
        ("4U7", Decimal("4.7e-6")),
        # 'MF' and 'MFD' on old schematics are microfarads.
        ("10MFD", Decimal("10e-6")),
        ("10mfd", Decimal("10e-6")),
        ("10MF", Decimal("10e-6")),
        ("4.7uFD", Decimal("4.7e-6")),
        # A lower-case 'mF' keeps its SI meaning, and 'M' alone is still mega.
        ("10mF", Decimal("10e-3")),
        ("10M", Decimal("10e6")),
        ("2M2", Decimal("2.2e6")),
        # A comma as decimal mark or thousands separator.
        ("0,068", Decimal("0.068")),
        ("0,068µF", Decimal("68e-9")),
        ("4,7k", Decimal("4700")),
        ("4,700", Decimal("4700")),
        ("1,000uF", Decimal("1000e-6")),
    ],
)
def test_upper_case_prefixes_old_farads_and_commas(
    val_str: str, expected: Decimal
) -> None:
    assert parse_value_to_decimal(val_str) == expected


@pytest.mark.parametrize("token", ["4N7", "1N4148", "2N3904", "4N35", "1,2,3"])
def test_upper_case_n_between_digits_is_a_part_number(token: str) -> None:
    # Diodes, transistors and optocouplers are named with an upper-case N.
    assert parse_value_to_decimal(token) is None


def test_a_value_in_another_unit_is_refused() -> None:
    assert parse_value_to_decimal("25V") == Decimal(25)
    assert parse_value_to_decimal("25V", Unit.FARAD) is None
    assert parse_value_to_decimal("100kΩ", Unit.FARAD) is None
    assert parse_value_to_decimal("100nF", Unit.OHM) is None
    assert parse_value_to_decimal("100kΩ", Unit.OHM) == Decimal(100000)
    assert parse_value_to_decimal("47 ohm", Unit.OHM) == Decimal(47)
    assert parse_value_to_decimal("100nF", Unit.FARAD) == Decimal("100e-9")
    # A value with no unit written is read in the part's unit.
    assert parse_value_to_decimal("100n", Unit.FARAD) == Decimal("100e-9")
