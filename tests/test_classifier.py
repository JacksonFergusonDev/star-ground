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
