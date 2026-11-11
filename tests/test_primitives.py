"""Characterization golden unit tests for core parsing primitives.

These tests lock down the exact baseline behavior of:
- `parse_value_to_decimal`
- `expand_refs`
- `categorize_part`
- `natural_sort_key`

Part of Phase 0 safety net ahead of Milestone 2 grammar refactoring.
"""

from decimal import Decimal
from typing import cast

import pytest

from star_ground.bom_lib.classifier import (
    categorize_part,
    key_quantity,
    normalize_value_to_quantity,
)
from star_ground.bom_lib.enums import ComponentCategory, ComponentSpec
from star_ground.bom_lib.manager import sort_inventory
from star_ground.bom_lib.sourcing import get_buy_details, get_spec_type
from star_ground.bom_lib.types import (
    CategorizationResult,
    ComponentKey,
    Inventory,
    RefDesignator,
)
from star_ground.bom_lib.units import Unit, Value, farads, ohms
from star_ground.bom_lib.utils import (
    expand_refs,
    is_ref_range,
    natural_sort_key,
    parse_value_to_decimal,
    value_to_search_string,
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


def test_expand_refs_reversed_lettered_and_padded_ranges() -> None:
    # Each was left as written, then read as one named control: a pot.
    assert expand_refs("R5-R1") == ["R1", "R2", "R3", "R4", "R5"]
    assert expand_refs("R1A-R1D") == ["R1A", "R1B", "R1C", "R1D"]
    assert expand_refs("R01-R03") == ["R01", "R02", "R03"]
    assert expand_refs("R08-R10") == ["R08", "R09", "R10"]


def test_expand_refs_shorthand_continuation() -> None:
    # 'C1-C3, 5-7' gave a literal designator '5-7'.
    assert expand_refs("C1-C3, 5-7") == ["C1", "C2", "C3", "C5", "C6", "C7"]


def test_unexpandable_ranges_are_told_apart() -> None:
    for ref in ("R1-R60", "R1-C3", "R1A-R2B"):
        assert expand_refs(ref) == [ref]
        assert is_ref_range(ref)
    for ref in ("R1", "VOLUME", "OSC-SW", "R1-", "1990-2000"):
        assert not is_ref_range(ref)


def test_designators_with_thousands_of_digits_are_no_numbers() -> None:
    # Python refuses to convert more than 4300 digits to an int: this text
    # raised ValueError, and the builder was told Star Ground hit a bug.
    digits = "1" * 5000
    assert expand_refs(f"R{digits}-2") == [f"R{digits}-2"]
    assert is_ref_range(f"R{digits}-2")
    assert natural_sort_key(f"R{digits}")[0] == "R"
    parsed = RefDesignator.from_string(f"R{digits}")
    assert parsed.number is None
    assert str(parsed) == f"R{digits}"
    assert RefDesignator.from_string("R1234") == RefDesignator("R", 1234)


@pytest.mark.parametrize(
    ("value", "written"),
    [
        # A resistance under one ohm was read as a capacitance: '470000u'.
        (ohms("0.47"), "0.47"),
        (ohms("4.7"), "4.7"),
        # Under a picofarad was rounded to '0'.
        (farads("0.5e-12"), "0.5p"),
        (farads("100e-9"), "100n"),
        (farads("4.7e-6"), "4.7u"),
        (ohms("1500"), "1.5k"),
        (ohms("0"), "0"),
    ],
)
def test_values_are_written_in_their_unit(value: Value, written: str) -> None:
    assert value_to_search_string(value) == written


# --- categorize_part Tests ---


def test_categorize_part_resistors() -> None:
    """Verifies resistor classification branches including standard R prefix and CLR."""
    assert categorize_part("R1", "10k") == CategorizationResult(
        ComponentCategory.RESISTORS, "10k", None
    )
    assert categorize_part("CLR", "4.7k") == CategorizationResult(
        ComponentCategory.RESISTORS, "4.7k", None
    )
    assert categorize_part("R10", "100") == CategorizationResult(
        ComponentCategory.RESISTORS, "100", None
    )


def test_categorize_part_capacitors() -> None:
    """Verifies capacitor classification branch."""
    assert categorize_part("C1", "100n") == CategorizationResult(
        ComponentCategory.CAPACITORS, "100n", None
    )
    assert categorize_part("C20", "4.7u") == CategorizationResult(
        ComponentCategory.CAPACITORS, "4.7u", None
    )


def test_categorize_part_potentiometer_via_taper() -> None:
    """Verifies potentiometer detection triggered by taper code in value."""
    assert categorize_part("VR1", "B100k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "B100k", None
    )
    assert categorize_part("RV1", "10k-A") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "10k-A", None
    )
    # A numbered standard designator outranks the value-shape heuristic:
    # '1N34A' ends in a taper letter but D1 is a diode.
    assert categorize_part("D1", "1N34A") == CategorizationResult(
        ComponentCategory.DIODES, "1N34A", None
    )
    assert categorize_part("C3", "1u MLCC") == CategorizationResult(
        ComponentCategory.CAPACITORS, "1u MLCC", None
    )
    assert categorize_part("POT1", "A100k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "A100k", None
    )


def test_categorize_part_potentiometer_via_name() -> None:
    """Verifies potentiometer detection triggered by known knob/control name."""
    assert categorize_part("VOLUME", "100k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "100k", None
    )
    assert categorize_part("GAIN", "10k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "10k", None
    )
    assert categorize_part("TONE", "50k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "50k", None
    )


def test_categorize_part_switch_ambiguity() -> None:
    """Verifies disambiguation between switches and potentiometers for ambiguous labels."""
    # Ambiguous label "LENGTH" with switch-like value -> Switches
    assert categorize_part("LENGTH", "SPDT ON-ON") == CategorizationResult(
        ComponentCategory.SWITCHES, "SPDT ON-ON", None
    )
    assert categorize_part("LENGTH", "SPDT") == CategorizationResult(
        ComponentCategory.SWITCHES, "SPDT", None
    )

    # Ambiguous label "LENGTH" with resistance/pot value -> Potentiometers fallback
    assert categorize_part("LENGTH", "100k") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "100k", None
    )

    # Explicit switch prefix -> Switches
    assert categorize_part("SW1", "DPDT") == CategorizationResult(
        ComponentCategory.SWITCHES, "DPDT", None
    )


def test_categorize_part_ic_with_socket() -> None:
    """Verifies IC classification with automatic DIP socket injection."""
    expected_injection = ComponentKey(
        ComponentCategory.HARDWARE_MISC, "DIP SOCKET (Check Size)"
    )
    assert categorize_part("IC1", "TL072") == CategorizationResult(
        ComponentCategory.ICS, "TL072", expected_injection
    )
    assert categorize_part("U1", "NE5532") == CategorizationResult(
        ComponentCategory.ICS, "NE5532", expected_injection
    )
    assert categorize_part("OP1", "LM308") == CategorizationResult(
        ComponentCategory.ICS, "LM308", expected_injection
    )


def test_categorize_part_ic_without_socket_keywords() -> None:
    """Verifies IC classification skipping socket injection for regulators/modules/reverb."""
    assert categorize_part("U1", "78L05 REGULATOR") == CategorizationResult(
        ComponentCategory.ICS, "78L05 REGULATOR", None
    )
    assert categorize_part("U2", "L78L05") == CategorizationResult(
        ComponentCategory.ICS, "L78L05", None
    )
    assert categorize_part("IC1", "BTDR-2H REVERB") == CategorizationResult(
        ComponentCategory.ICS, "BTDR-2H REVERB", None
    )
    assert categorize_part("U3", "DSP MODULE") == CategorizationResult(
        ComponentCategory.ICS, "DSP MODULE", None
    )


def test_categorize_part_ldr() -> None:
    """Verifies LDR (Light Dependent Resistor) optoelectronics branch."""
    assert categorize_part("LDR1", "5mm") == CategorizationResult(
        ComponentCategory.OPTOELECTRONICS, "5mm", None
    )
    assert categorize_part("LDR2", "GL5528") == CategorizationResult(
        ComponentCategory.OPTOELECTRONICS, "GL5528", None
    )


def test_categorize_part_other_branches() -> None:
    """Verifies diodes, transistors, crystals, and invalid parts."""
    assert categorize_part("D1", "1N4148") == CategorizationResult(
        ComponentCategory.DIODES, "1N4148", None
    )
    assert categorize_part("LED1", "3mm Red") == CategorizationResult(
        ComponentCategory.DIODES, "3mm Red LED", None
    )
    assert categorize_part("Q1", "2N3904") == CategorizationResult(
        ComponentCategory.TRANSISTORS, "2N3904", None
    )
    assert categorize_part("X1", "16MHz") == CategorizationResult(
        ComponentCategory.CRYSTALS_OSCILLATORS, "16MHz", None
    )
    assert categorize_part("J1", "1/4 Mono Jack") == CategorizationResult(
        ComponentCategory.HARDWARE_MISC, "1/4 Mono Jack", None
    )
    # Unknown labels are rejected unless the value marks them as a control...
    assert categorize_part("INVALID_PREFIX", "TL072") is None
    # ...in which case a resistance makes them a pot or trimmer (INT_BIAS 50K).
    assert categorize_part("INT_BIAS", "50K") == CategorizationResult(
        ComponentCategory.POTENTIOMETERS, "50K", None
    )


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


# --- normalize_value_to_quantity Tests ---


def test_normalize_value_to_quantity_resistors() -> None:
    """Resistors are read as values in ohms."""
    qty = normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k")
    assert isinstance(qty, Value)
    assert qty.unit == Unit.OHM
    assert qty.magnitude == Decimal("10000")


def test_normalize_value_to_quantity_capacitors() -> None:
    """Capacitors are read as values in farads."""
    qty = normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100n")
    assert isinstance(qty, Value)
    assert qty.unit == Unit.FARAD
    assert qty.magnitude == Decimal("1e-7")


def test_normalize_value_to_quantity_other_categories() -> None:
    """Verifies that non-passive numeric values return bare Decimals."""
    qty = normalize_value_to_quantity(ComponentCategory.POTENTIOMETERS, "100k")
    assert isinstance(qty, Decimal)
    assert qty == Decimal("100000")


def test_normalize_value_to_quantity_invalid() -> None:
    """Verifies that dimensions (mm) and unparseable values return None."""
    assert normalize_value_to_quantity(ComponentCategory.RESISTORS, "5mm LDR") is None
    assert normalize_value_to_quantity(ComponentCategory.ICS, "TL072") is None
    assert normalize_value_to_quantity(ComponentCategory.RESISTORS, "") is None


# --- Inventory val_qty Tests ---


def test_key_quantity() -> None:
    """A part's value with its unit, derived from its key."""
    assert key_quantity(ComponentKey(ComponentCategory.RESISTORS, "10k")) == ohms(
        "10000"
    )
    assert key_quantity(ComponentKey(ComponentCategory.CAPACITORS, "100n")) == farads(
        "1e-7"
    )
    assert key_quantity(ComponentKey(ComponentCategory.ICS, "TL072")) is None
    assert key_quantity(ComponentKey(ComponentCategory.PCB, "1981 DRV")) is None


# --- sort_inventory Tests ---


def test_sort_inventory_numerical_order() -> None:
    """Verifies that sort_inventory sorts component values by their exact numeric magnitude."""
    inv = Inventory()
    k_100k = ComponentKey(ComponentCategory.RESISTORS, "100k")
    k_1k = ComponentKey(ComponentCategory.RESISTORS, "1k")
    k_10k = ComponentKey(ComponentCategory.RESISTORS, "10k")
    k_1m = ComponentKey(ComponentCategory.RESISTORS, "1M")

    inv.add_part("P1", k_100k, "R3")
    inv.add_part("P1", k_1k, "R1")
    inv.add_part("P1", k_10k, "R2")
    inv.add_part("P1", k_1m, "R4")

    sorted_parts = [key for key, _ in sort_inventory(inv)]
    assert sorted_parts == [k_1k, k_10k, k_100k, k_1m]
    assert [str(k) for k in sorted_parts] == [
        "Resistors | 1k",
        "Resistors | 10k",
        "Resistors | 100k",
        "Resistors | 1M",
    ]


def test_sort_inventory_pcbs_by_name() -> None:
    """PCBs sort by name, even when the name starts with a number."""
    inv = Inventory()
    k_drv = ComponentKey(ComponentCategory.PCB, "1981 DRV")
    k_muff = ComponentKey(ComponentCategory.PCB, "Big Muff")

    inv.add_part("P1", k_muff, "PCB")
    inv.add_part("P2", k_drv, "PCB")

    assert [key for key, _ in sort_inventory(inv)] == [k_drv, k_muff]


def test_sort_inventory_capacitors_order() -> None:
    """Verifies capacitor sorting across sub-microfarad ranges."""
    inv = Inventory()
    k_100u = ComponentKey(ComponentCategory.CAPACITORS, "100u")
    k_100p = ComponentKey(ComponentCategory.CAPACITORS, "100p")
    k_1u = ComponentKey(ComponentCategory.CAPACITORS, "1u")
    k_100n = ComponentKey(ComponentCategory.CAPACITORS, "100n")

    inv.add_part("P1", k_100u, "C4")
    inv.add_part("P1", k_100p, "C1")
    inv.add_part("P1", k_1u, "C3")
    inv.add_part("P1", k_100n, "C2")

    sorted_parts = [key for key, _ in sort_inventory(inv)]
    assert sorted_parts == [k_100p, k_100n, k_1u, k_100u]
    assert [str(k) for k in sorted_parts] == [
        "Capacitors | 100p",
        "Capacitors | 100n",
        "Capacitors | 1u",
        "Capacitors | 100u",
    ]


# --- Sourcing exact equality tests ---


def test_get_spec_type_exact() -> None:
    """Verifies get_spec_type dielectric classification at boundary conditions."""
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "100p",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100p"),
        )
        == ComponentSpec.MLCC
    )
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "1n",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1n"),
        )
        == ComponentSpec.BOX_FILM
    )
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "1u",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1u"),
        )
        == ComponentSpec.BOX_FILM
    )
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "2.2u",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "2.2u"),
        )
        == ComponentSpec.ELECTROLYTIC
    )


def test_get_buy_details_exact_quantities() -> None:
    """Verifies get_buy_details exact matching on bulk and large capacitor thresholds."""
    # 100nF bulk buy
    rec_bulk = get_buy_details(
        ComponentCategory.CAPACITORS,
        "100n",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100n"),
    )
    assert rec_bulk.buy_qty == 11  # 1 + bulk_buffer (10)
    assert "A common bypass value: worth buying in bulk." in rec_bulk.note

    # 1uF large cap low buffer
    rec_1u = get_buy_details(
        ComponentCategory.CAPACITORS,
        "1u",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1u"),
    )
    assert rec_1u.buy_qty == 2  # 1 + large_buffer (1)
    assert "The document may mean an electrolytic" in rec_1u.note
