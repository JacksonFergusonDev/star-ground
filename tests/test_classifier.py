"""Tests for component classification of GuitarPCB test-document parts."""

from src.bom_lib.classifier import categorize_part
from src.bom_lib.enums import ComponentCategory


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
