"""Unit tests for the reference designator grammar in `src/bom_lib/designators.py`."""

import pytest

from src.bom_lib.designators import BOARD_REF, is_designator, parse_designator
from src.bom_lib.enums import ComponentCategory as Cat


@pytest.mark.parametrize(
    ("ref", "category"),
    [
        ("R1", Cat.RESISTORS),
        ("R1A", Cat.RESISTORS),  # channel suffix (Duo-Phase)
        ("D10a", Cat.DIODES),  # lowercase channel suffix (Shindig)
        ("IC_A", Cat.ICS),  # underscore name (Expresso)
        ("JACK_IN", Cat.HARDWARE_MISC),
        ("LED", Cat.DIODES),
        ("LEDA", Cat.DIODES),  # bare designator with channel letter
        ("CLR1", Cat.RESISTORS),  # not a capacitor
        ("TRIM1A", Cat.POTENTIOMETERS),
        ("TR1", Cat.POTENTIOMETERS),
        ("LDR1A", Cat.OPTOELECTRONICS),
        ("SW_C", Cat.SWITCHES),
        ("T1", Cat.HARDWARE_MISC),  # transformer
        ("K1", Cat.HARDWARE_MISC),  # relay
        ("V1", Cat.HARDWARE_MISC),  # vacuum tube
        ("RV1", Cat.POTENTIOMETERS),  # KiCad variable resistor (Sheepylove)
        ("PNP1", Cat.TRANSISTORS),  # Twin Face
        ("NPN2", Cat.TRANSISTORS),
        ("LEDR", Cat.RESISTORS),  # Aion FX LED resistor, not an LED
        ("LEDRB", Cat.RESISTORS),
        ("RPD", Cat.RESISTORS),  # Aion FX input pulldown
        ("RPD2", Cat.RESISTORS),
        ("RX1", Cat.RESISTORS),  # Aion FX optional-mod parts
        ("CX5", Cat.CAPACITORS),
        ("CX", Cat.CAPACITORS),  # GuitarPCB Dr Rock's optional op-amp cap
        ("R-A", Cat.RESISTORS),  # GuitarPCB D'Lay
        ("SW 1", Cat.SWITCHES),  # GuitarPCB EA Tremelo
        ("DA", Cat.DIODES),  # GuitarPCB Easy Order: two LEDs for one bi-colour
        ("QX1", Cat.TRANSISTORS),  # Aion FX legacy L5 Preamp
        ("RG1", Cat.ICS),  # voltage regulator (Aion FX legacy Blueshift)
        ("Z1", Cat.DIODES),  # zener
        ("XFM1", Cat.HARDWARE_MISC),  # transformer
        ("r1", Cat.RESISTORS),
    ],
)
def test_designators_and_their_categories(ref: str, category: Cat) -> None:
    designator = parse_designator(ref)
    assert designator is not None
    assert designator.category == category


def test_l_prefix_is_decided_by_value() -> None:
    designator = parse_designator("L1")
    assert designator is not None
    assert designator.category is None


@pytest.mark.parametrize(
    "token",
    [
        "VOLUME",
        "TREBLE",
        "OSC-SW",
        "INT_BIAS",
        "CTRL1",
        "RATEA",
        "TL072",
        "2N3904",
        "C10K",
        "C1M",
    ],
)
def test_control_names_and_values_are_not_designators(token: str) -> None:
    assert not is_designator(token)


@pytest.mark.parametrize("token", ["R13", "C4", "IC_A", "LED2", "R1A"])
def test_board_refs(token: str) -> None:
    assert BOARD_REF.fullmatch(token)


@pytest.mark.parametrize("token", ["TL072", "BS170", "J201", "C10K", "MPSA18"])
def test_part_numbers_are_not_board_refs(token: str) -> None:
    assert not BOARD_REF.fullmatch(token)


def test_alternate_clipping_diodes() -> None:
    # Aion FX legacy Crescent: 'DX1-DX4  1N914'.
    from src.bom_lib.designators import is_designator

    assert is_designator("DX1")


def test_a_voltage_regulator() -> None:
    # GuitarPCB Delay: 'REG  L78L05'.
    from src.bom_lib.designators import parse_designator
    from src.bom_lib.enums import ComponentCategory

    designator = parse_designator("REG")
    assert designator is not None
    assert designator.category == ComponentCategory.ICS


def test_lower_case_lettered_parts() -> None:
    # Tonepad letters the parts that differ between builds: 'Ra - 100', 'Cb - 1 µF'.
    for token, category in (("Ra", Cat.RESISTORS), ("Cd", Cat.CAPACITORS)):
        parsed = parse_designator(token)
        assert parsed is not None
        assert parsed.category == category
    # Upper case is a word, not a part.
    assert not is_designator("RE")
    assert not is_designator("CD")
