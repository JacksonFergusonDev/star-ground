"""Characterization golden unit tests for core parsing primitives.

These tests lock down the exact baseline behavior of:
- `parse_value_to_float`
- `expand_refs`
- `categorize_part`
- `natural_sort_key`

Part of Phase 0 safety net ahead of Milestone 2 grammar refactoring.
"""

from decimal import Decimal
from typing import cast

import pytest

from src.bom_lib.classifier import categorize_part
from src.bom_lib.utils import (
    expand_refs,
    natural_sort_key,
    parse_value_to_decimal,
    parse_value_to_float,
)

# --- parse_value_to_decimal Tests ---


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
        # Bare numbers
        ("100", Decimal("100")),
        ("0.1", Decimal("0.1")),
        ("0", Decimal("0")),
        ("470", Decimal("470")),
    ],
)
def test_parse_value_to_decimal_valid(val_str: str, expected: Decimal) -> None:
    """Verifies standard, sandwich, and bare number parsing to Decimal."""
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
    ],
)
def test_parse_value_to_decimal_none_returns(val_str: str) -> None:
    """Verifies that invalid or empty strings return None."""
    assert parse_value_to_decimal(val_str) is None


def test_parse_value_to_decimal_falsy_none() -> None:
    """Verifies that None/empty input safely returns None."""
    assert parse_value_to_decimal(cast(str, None)) is None


# --- parse_value_to_float Tests ---


@pytest.mark.parametrize(
    ("val_str", "expected"),
    [
        # Standard notation
        ("10k", 10000.0),
        ("4.7u", 4.7e-6),
        ("100n", 1e-7),
        ("2.2M", 2.2e6),
        ("100p", 100e-12),
        ("10m", 0.01),
        ("1G", 1e9),
        ("4.7µ", 4.7e-6),
        # Sandwich notation (BS 1852)
        ("1k5", 1500.0),
        ("4n7", 4.7e-9),
        ("2M2", 2200000.0),
        ("4u7", 4.7e-6),
        ("2p2", 2.2e-12),
        # Bare numbers
        ("100", 100.0),
        ("0.1", 0.1),
        ("0", 0.0),
        ("470", 470.0),
    ],
)
def test_parse_value_to_float_valid(val_str: str, expected: float) -> None:
    """Verifies standard, sandwich, and bare number parsing."""
    result = parse_value_to_float(val_str)
    assert result is not None
    assert pytest.approx(result) == expected


@pytest.mark.parametrize(
    "val_str",
    [
        "",
        "   ",
        "FOOBAR",
        "R1",
        "hello",
    ],
)
def test_parse_value_to_float_none_returns(val_str: str) -> None:
    """Verifies that invalid or empty strings return None."""
    assert parse_value_to_float(val_str) is None


def test_parse_value_to_float_falsy_none() -> None:
    """Verifies that None/empty input safely returns None."""
    assert parse_value_to_float(cast(str, None)) is None


# --- expand_refs Tests ---


def test_expand_refs_simple_ranges() -> None:
    """Verifies expansion of simple component reference ranges."""
    assert expand_refs("R1-R4") == ["R1", "R2", "R3", "R4"]
    assert expand_refs("C1-3") == ["C1", "C2", "C3"]
    assert expand_refs("D10-D12") == ["D10", "D11", "D12"]
    assert expand_refs("Q1-Q2") == ["Q1", "Q2"]


def test_expand_refs_sanity_cap_under_50() -> None:
    """Verifies that ranges under the 50-item limit expand fully."""
    # delta = 49 < 50, generates 50 elements R1 to R50
    expanded = expand_refs("R1-R50")
    assert len(expanded) == 50
    assert expanded[0] == "R1"
    assert expanded[-1] == "R50"


def test_expand_refs_sanity_cap_over_or_equal_50() -> None:
    """Verifies that ranges >= 50 items are not expanded to prevent runaway allocations."""
    # delta = 50 >= 50 -> unexpanded
    assert expand_refs("R1-R51") == ["R1-R51"]
    # delta = 99 >= 50 -> unexpanded
    assert expand_refs("R1-R100") == ["R1-R100"]
    # Year/date range check
    assert expand_refs("1990-2000") == ["1990-2000"]


def test_expand_refs_non_ranges_and_malformed() -> None:
    """Verifies handling of single refs, non-ranges, and malformed strings."""
    assert expand_refs("R1") == ["R1"]
    assert expand_refs("VOLUME") == ["VOLUME"]
    assert expand_refs("U1") == ["U1"]
    assert expand_refs("R1-") == ["R1-"]
    assert expand_refs("-R4") == ["-R4"]


# --- categorize_part Tests ---


def test_categorize_part_resistors() -> None:
    """Verifies resistor classification branches including standard R prefix and CLR."""
    assert categorize_part("R1", "10k") == ("Resistors", "10k", None)
    assert categorize_part("CLR", "4.7k") == ("Resistors", "4.7k", None)
    assert categorize_part("R10", "100") == ("Resistors", "100", None)


def test_categorize_part_capacitors() -> None:
    """Verifies capacitor classification branch."""
    assert categorize_part("C1", "100n") == ("Capacitors", "100n", None)
    assert categorize_part("C20", "4.7u") == ("Capacitors", "4.7u", None)


def test_categorize_part_potentiometer_via_taper() -> None:
    """Verifies potentiometer detection triggered by taper code in value."""
    assert categorize_part("VR1", "B100k") == ("Potentiometers", "B100k", None)
    assert categorize_part("R1", "10k-A") == ("Potentiometers", "10k-A", None)
    assert categorize_part("POT1", "A100k") == ("Potentiometers", "A100k", None)


def test_categorize_part_potentiometer_via_name() -> None:
    """Verifies potentiometer detection triggered by known knob/control name."""
    assert categorize_part("VOLUME", "100k") == ("Potentiometers", "100k", None)
    assert categorize_part("GAIN", "10k") == ("Potentiometers", "10k", None)
    assert categorize_part("TONE", "50k") == ("Potentiometers", "50k", None)


def test_categorize_part_switch_ambiguity() -> None:
    """Verifies disambiguation between switches and potentiometers for ambiguous labels."""
    # Ambiguous label "LENGTH" with switch-like value -> Switches
    assert categorize_part("LENGTH", "SPDT ON-ON") == ("Switches", "SPDT ON-ON", None)
    assert categorize_part("LENGTH", "SPDT") == ("Switches", "SPDT", None)

    # Ambiguous label "LENGTH" with resistance/pot value -> Potentiometers fallback
    assert categorize_part("LENGTH", "100k") == ("Potentiometers", "100k", None)

    # Explicit switch prefix -> Switches
    assert categorize_part("SW1", "DPDT") == ("Switches", "DPDT", None)


def test_categorize_part_ic_with_socket() -> None:
    """Verifies IC classification with automatic DIP socket injection."""
    expected_injection = "Hardware/Misc | DIP SOCKET (Check Size)"
    assert categorize_part("IC1", "TL072") == ("ICs", "TL072", expected_injection)
    assert categorize_part("U1", "NE5532") == ("ICs", "NE5532", expected_injection)
    assert categorize_part("OP1", "LM308") == ("ICs", "LM308", expected_injection)


def test_categorize_part_ic_without_socket_keywords() -> None:
    """Verifies IC classification skipping socket injection for regulators/modules/reverb."""
    assert categorize_part("U1", "78L05 REGULATOR") == ("ICs", "78L05 REGULATOR", None)
    assert categorize_part("U2", "L78L05") == ("ICs", "L78L05", None)
    assert categorize_part("IC1", "BTDR-2H REVERB") == ("ICs", "BTDR-2H REVERB", None)
    assert categorize_part("U3", "DSP MODULE") == ("ICs", "DSP MODULE", None)


def test_categorize_part_ldr() -> None:
    """Verifies LDR (Light Dependent Resistor) optoelectronics branch."""
    assert categorize_part("LDR1", "5mm") == ("Optoelectronics", "5mm", None)
    assert categorize_part("LDR2", "GL5528") == ("Optoelectronics", "GL5528", None)


def test_categorize_part_other_branches() -> None:
    """Verifies diodes, transistors, crystals, and invalid parts."""
    assert categorize_part("D1", "1N4148") == ("Diodes", "1N4148", None)
    assert categorize_part("LED1", "3mm Red") == ("Diodes", "3mm Red", None)
    assert categorize_part("Q1", "2N3904") == ("Transistors", "2N3904", None)
    assert categorize_part("X1", "16MHz") == ("Crystals/Oscillators", "16MHz", None)
    assert categorize_part("J1", "1/4 Mono Jack") == (
        "Hardware/Misc",
        "1/4 Mono Jack",
        None,
    )
    assert categorize_part("INVALID_PREFIX", "100k") == (None, None, None)


# --- natural_sort_key Tests ---


def test_natural_sort_key_ordering() -> None:
    """Verifies that natural sort correctly orders R2 before R10 (unlike lexicographical sort)."""
    assert natural_sort_key("R2") < natural_sort_key("R10")
    assert natural_sort_key("R1") < natural_sort_key("R2")
    assert natural_sort_key("SW1") < natural_sort_key("SW10")


def test_natural_sort_key_list_sorting() -> None:
    """Verifies natural sort ordering across a mixed list of references."""
    unsorted = ["R100", "R2", "R10", "R1", "R20"]
    expected = ["R1", "R2", "R10", "R20", "R100"]
    assert sorted(unsorted, key=natural_sort_key) == expected

    mixed = ["SW10", "C2", "R1", "SW2", "C10", "C1"]
    expected_mixed = ["C1", "C2", "C10", "R1", "SW2", "SW10"]
    assert sorted(mixed, key=natural_sort_key) == expected_mixed
