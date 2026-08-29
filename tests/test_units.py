"""Unit tests for the physical units registry."""

import pint

from src.bom_lib.units import ureg


def test_ureg_instance() -> None:
    """Verifies that ureg is a valid UnitRegistry."""
    assert isinstance(ureg, pint.UnitRegistry)


def test_ureg_basic_quantities() -> None:
    """Verifies standard electrical units exist and can be instantiated."""
    resistor = 1000 * ureg.ohm
    assert resistor.magnitude == 1000
    assert resistor.units == ureg.ohm

    cap = 100 * ureg.nanofarad
    assert cap.to(ureg.microfarad).magnitude == 0.1
