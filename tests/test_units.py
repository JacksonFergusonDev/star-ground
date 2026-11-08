"""Component values and how they print."""

from decimal import Decimal

import pytest

from star_ground.bom_lib.units import Unit, Value, farads, ohms


@pytest.mark.parametrize(
    ("value", "printed"),
    [
        (ohms("470"), "470 Ω"),
        (ohms("4700"), "4.7 kΩ"),
        (ohms("10000"), "10 kΩ"),
        (ohms("1000000"), "1 MΩ"),
        (ohms("2.2"), "2.2 Ω"),
        (farads("100e-12"), "100 pF"),
        (farads("2.2e-9"), "2.2 nF"),
        (farads("100e-9"), "100 nF"),
        (farads("1e-6"), "1 µF"),
        (farads("100e-6"), "100 µF"),
        (farads("4700e-6"), "4.7 mF"),
        (ohms("0"), "0 Ω"),
    ],
)
def test_a_value_prints_as_builders_write_it(value: Value, printed: str) -> None:
    assert str(value) == printed


def test_values_order_and_compare_within_a_unit() -> None:
    assert farads("1e-9") < farads("100e-9") <= farads("1e-7")
    assert farads("1E-7") == farads("100e-9")
    assert ohms("10000") == Value(Decimal(10000), Unit.OHM)


def test_values_in_different_units_do_not_compare() -> None:
    assert ohms("1") != farads("1")
    with pytest.raises(TypeError):
        _ = ohms("1") < farads("1")
