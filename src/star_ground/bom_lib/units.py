"""Component values with their units.

A resistor's or capacitor's value is a `Value`: its magnitude in base units
(ohms, farads) as a `Decimal`, and its unit. It compares only with a value of
the same unit, and prints the way builders write values ('4.7 kΩ', '100 nF').
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import total_ordering

__all__ = ["Unit", "Value", "farads", "ohms"]


class Unit(StrEnum):
    """The units a component value is measured in."""

    OHM = "Ω"
    FARAD = "F"


# SI prefixes by power of a thousand, smallest first.
_PREFIXES = {-4: "p", -3: "n", -2: "µ", -1: "m", 0: "", 1: "k", 2: "M", 3: "G"}
_THOUSAND = Decimal(1000)


@total_ordering
@dataclass(frozen=True, slots=True)
class Value:
    """A component value: a magnitude in base units, with its unit.

    Attributes:
        magnitude: The value in ohms or farads (10000 for '10k').
        unit: Its unit.
    """

    magnitude: Decimal
    unit: Unit

    def __lt__(self, other: object) -> bool:
        """Orders values of the same unit by magnitude.

        Raises:
            TypeError: If `other` is not a value in the same unit.
        """
        if not isinstance(other, Value) or other.unit != self.unit:
            return NotImplemented
        return self.magnitude < other.magnitude

    def __str__(self) -> str:
        """The value as builders write it: '4.7 kΩ', '100 nF', '1 µF'."""
        if self.magnitude == 0:
            return f"0 {self.unit}"
        power = 0
        mantissa = abs(self.magnitude)
        while mantissa >= _THOUSAND and power < max(_PREFIXES):
            mantissa /= _THOUSAND
            power += 1
        while mantissa < 1 and power > min(_PREFIXES):
            mantissa *= _THOUSAND
            power -= 1
        sign = "-" if self.magnitude < 0 else ""
        digits = format(mantissa.normalize(), "f")
        return f"{sign}{digits} {_PREFIXES[power]}{self.unit}"


def ohms(magnitude: Decimal | str) -> Value:
    """A resistance, in ohms."""
    return Value(Decimal(magnitude), Unit.OHM)


def farads(magnitude: Decimal | str) -> Value:
    """A capacitance, in farads."""
    return Value(Decimal(magnitude), Unit.FARAD)
