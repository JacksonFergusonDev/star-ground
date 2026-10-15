"""Unit tests for TYPE/NOTES column enrichment and value normalization."""

import pytest

from src.bom_lib import ComponentCategory, categorize_part, create_empty_stats
from src.bom_lib.parser import (
    _table_entries,
    category_from_type,
    enrich_value_from_type,
    rating_from_notes,
)
from src.bom_lib.utils import float_to_search_string


@pytest.mark.parametrize(
    ("value", "type_text", "expected"),
    [
        ("RED", "3mm Red LED, Diffused", "3mm Red LED"),
        ("LED", "Indicator LED", "Indicator LED"),
        ("SPST", "SPST Momentary Footswitch (Normally Open)", "SPST Momentary"),
        ("ON/ON", "Toggle switch, SPDT 2-position (ON/ON)", "SPDT ON/ON"),
        ("GE", "Germanium diode, 1N34A", "1N34A"),
        ("A-4.5W-K", "Non-latching 4.5V relay", "A-4.5W-K"),
        ("10K", "Resistor, 1/4W", "10K"),
    ],
)
def test_enrich_value_from_type(value: str, type_text: str, expected: str) -> None:
    assert enrich_value_from_type(value, type_text) == expected


@pytest.mark.parametrize(
    ("value", "type_text", "notes", "expected"),
    [
        ("DPDT", "Toggle switch, DPDT", "2-position (ON/ON)", "DPDT ON/ON"),
        ("SPDT", "Toggle switch, SPDT", "3-position (ON/OFF/ON)", "SPDT ON/OFF/ON"),
        ("220n", "Tantalum capacitor, 5mm", "", "220n Tantalum"),
        ("10u", "Ceramic capacitor, MLCC", "", "10u MLCC"),
        ("1u MLCC", "MLCC capacitor, 7.2 x 2.5mm", "", "1u MLCC"),
        ("10K", "Resistor, 1/4W", "* LED current limiting resistor", "10K"),
        (
            "B50K",
            "16mm right-angle PCB mount pot",
            "* Dual gang potentiometer",
            "B50K Dual",
        ),
        (
            "B100K DUAL",
            "16mm right-angle PCB mount pot",
            "*Dual gang potentiometer",
            "B100K DUAL",
        ),
    ],
)
def test_enrich_value_from_type_and_notes(
    value: str, type_text: str, notes: str, expected: str
) -> None:
    assert enrich_value_from_type(value, type_text, notes) == expected


def test_current_rating_is_not_a_pot_value() -> None:
    # Auditorium: a 250mA poly fuse is not a 250M audio-taper pot.
    fuse = "Resettable Poly Fuse (250mA)"
    result = categorize_part("POLYFUSE", fuse, category_from_type(fuse))
    assert result is not None
    assert result.category == ComponentCategory.HARDWARE_MISC


def test_category_from_type() -> None:
    assert (
        category_from_type("Neutrik 1/4in Insulated Stereo Jack")
        == ComponentCategory.HARDWARE_MISC
    )
    assert category_from_type("Resistor, 1/4W") is None


def test_named_part_uses_type_hint() -> None:
    result = categorize_part("RECV", "NMJ6HCD2", ComponentCategory.HARDWARE_MISC)
    assert result is not None
    assert result.category == ComponentCategory.HARDWARE_MISC


@pytest.mark.parametrize(
    ("ref", "value", "notes", "expected"),
    [
        ("C102", "10u", "25V Minimum Voltage", "10u (25V minimum)"),
        ("C102", "10u (35V or higher)", "25V", "10u (35V or higher)"),
        ("R1", "10K", "25V", "10K"),
        ("C1", "100n", "", "100n"),
    ],
)
def test_rating_from_notes(ref: str, value: str, notes: str, expected: str) -> None:
    assert rating_from_notes(ref, value, notes) == expected


def test_trimmer_notes_are_not_part_of_the_value() -> None:
    for raw in ("10K Trim (3362P)", "100K (3362P Trim)"):
        result = categorize_part("TR1", raw)
        assert result is not None
        assert result.clean_value in ("10K", "100K")


def test_search_strings_keep_significant_digits() -> None:
    assert float_to_search_string(4650.0) == "4.65k"
    assert float_to_search_string(68100.0) == "68.1k"
    assert float_to_search_string(4700.0) == "4.7k"
    assert float_to_search_string(2.2e-9) == "2.2n"


def test_table_values_drop_optional_markers_but_keep_placeholders() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["R24", "*27K*", "Resistor, 1/4W", "* Omitted in some units"],
        ["D101", "**", "Zener diode", "** Refer to page 2 **"],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.value) for e in entries] == [("R24", "27K"), ("D101", "**")]


def test_table_or_row_marks_the_next_entry() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["D10", "DI1510", "Full-wave bridge rectifier", ""],
        ["", "", "OR", ""],
        ["D10a", "1N4007", "Standard recovery diode, DO-41", ""],
        ["D10b", "1N4007", "Standard recovery diode, DO-41", ""],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.follows_or) for e in entries] == [
        ("D10", False),
        ("D10a", True),
        ("D10b", False),
    ]
