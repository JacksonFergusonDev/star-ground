"""Tests for component classification of GuitarPCB test-document parts."""

import pytest

from src.bom_lib.classifier import categorize_part
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.parser import category_from_type


def test_trimmer_value_leaves_no_dash() -> None:
    # GuitarPCB NostalgiTone Chorus: 'VOL | Trim - 50k'.
    vol = categorize_part("VOL", "Trim - 50k")
    assert vol is not None
    assert vol.clean_value == "50k"


def test_hardware_rows_are_parts() -> None:
    # Ancestral Apparition's reverb brick, Test Rig's DC jack and terminal.
    for ref, value in (
        ("BTDR-2H", "LONG"),
        ("DC POWER", "2.1MM DC"),
        ("CONN1", "4 WAY Speaker Terminal (spring)"),
    ):
        part = categorize_part(ref, value)
        assert part is not None
        assert part.category == ComponentCategory.HARDWARE_MISC


def test_named_parts_classified_by_their_values() -> None:
    # Aion FX legacy Blueshift and L5 Preamp.
    for ref, value, category in (
        ("OPTO", "H11F1", ComponentCategory.OPTOELECTRONICS),
        ("IN", "111X", ComponentCategory.HARDWARE_MISC),
        ("DC JACK", "miniature", ComponentCategory.HARDWARE_MISC),
        ("BYPASS", "5MM LED", ComponentCategory.DIODES),
        ("COMP", "5MM LED", ComponentCategory.DIODES),
    ):
        part = categorize_part(ref, value)
        assert part is not None
        assert part.category == category


def test_another_controls_name_is_no_value() -> None:
    # A drill template's knob labels read as 'LEVEL  BASS2' (legacy L5 Preamp).
    assert categorize_part("LEVEL", "BASS2") is None


def test_regulators_get_no_socket() -> None:
    for ref, value in (("RG1", "78L06"), ("RG2", "MC7915"), ("IC9", "LM7805")):
        part = categorize_part(ref, value)
        assert part is not None
        assert part.injected_key is None
    chip = categorize_part("IC1", "TL072")
    assert chip is not None
    assert chip.injected_key is not None


def test_lettered_led_resistor_follows_its_type() -> None:
    # Aion FX Ibex: 'LEDB | 4k7 | Metal film resistor'; a bare 'LEDB' stays an LED.
    part = categorize_part("LEDB", "4k7", ComponentCategory.RESISTORS)
    assert part is not None
    assert part.category == ComponentCategory.RESISTORS
    led = categorize_part("LEDB", "3mm red")
    assert led is not None
    assert led.category == ComponentCategory.DIODES


def test_a_pot_under_a_resistor_designator() -> None:
    # Tonepad: 'R7 - 100k lin pot', 'R13 - 500k Rev. Log'.
    for value in ("100k lin pot", "50k Log Pot", "500k Rev. Log", "1M pot"):
        result = categorize_part("R7", value)
        assert result is not None
        assert result.category == ComponentCategory.POTENTIOMETERS
    fixed = categorize_part("R7", "100k")
    assert fixed is not None
    assert fixed.category == ComponentCategory.RESISTORS


def test_capacitor_units_and_bare_fractions() -> None:
    # Tonepad: '0.068 µF' with a space, '0.001' with no unit at all.
    for value, clean in (
        ("0.068 µF", "68n"),
        ("100 pF", "100p"),
        ("0.001", "1n"),
        (".05", "50n"),
    ):
        result = categorize_part("C2", value)
        assert result is not None
        assert result.clean_value == clean


def test_a_value_restated_in_parentheses() -> None:
    # Tonepad: 'R5 - 8k2 (8.2k)'; a different value in parentheses stays.
    restated = categorize_part("R5", "8k2 (8.2k)")
    assert restated is not None
    assert restated.clean_value == "8.2k"
    other = categorize_part("R5", "10k (bias)")
    assert other is not None
    assert other.clean_value == "10k (bias)"


def test_a_trimmer_under_a_transistor_designator() -> None:
    # Aion FX Phobos: 'Q3B  50k trimmer' biases Q3.
    part = categorize_part("Q3B", "50k trimmer")
    assert part is not None
    assert part.category == ComponentCategory.POTENTIOMETERS


def test_a_pots_body_size_is_no_part_of_its_value() -> None:
    # Aion FX legacy Deimos: 'Fuzz  1kC 9mm'.
    part = categorize_part("FUZZ", "1kC 9mm")
    assert part is not None
    assert part.clean_value == "1kC"


def test_a_pole_count_dip_switch() -> None:
    # GuitarPCB Afterblaster: 'DIP1-DIP2  2-POLE_DIP'.
    part = categorize_part("DIP1", "2-POLE_DIP")
    assert part is not None
    assert part.category == ComponentCategory.SWITCHES


@pytest.mark.parametrize(
    ("ref", "value", "category"),
    [
        ("L1", "100mH", ComponentCategory.INDUCTORS),
        ("L1", "3mm LED", ComponentCategory.DIODES),  # some documents number LEDs L1-L4
        ("L1", "2174 lamp", ComponentCategory.HARDWARE_MISC),
        ("T1", "42TM018", ComponentCategory.TRANSFORMERS),
        ("XFMR1", "42TM002", ComponentCategory.TRANSFORMERS),
        ("T1", "10k Trim", ComponentCategory.POTENTIOMETERS),  # GuitarPCB trimmers
        ("K1", "TQ2-L-5V", ComponentCategory.RELAYS),
    ],
)
def test_inductors_transformers_and_relays_have_their_own_categories(
    ref: str, value: str, category: ComponentCategory
) -> None:
    part = categorize_part(ref, value)
    assert part is not None
    assert part.category == category


@pytest.mark.parametrize(
    ("type_text", "category"),
    [
        ("Inductor, axial", ComponentCategory.INDUCTORS),
        ("Audio transformer", ComponentCategory.TRANSFORMERS),
        ("Single-Coil Latching Relay, 5V", ComponentCategory.RELAYS),
    ],
)
def test_a_type_column_names_the_new_categories(
    type_text: str, category: ComponentCategory
) -> None:
    assert category_from_type(type_text) == category
