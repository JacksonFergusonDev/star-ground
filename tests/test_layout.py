"""Unit tests for column-aware listing extraction in `src/bom_lib/layout.py`."""

import pytest

from src.bom_lib import PDFWord
from src.bom_lib.layout import (
    Listing,
    clean_value,
    find_listings,
    group_lines,
    split_cells,
)
from src.bom_lib.parser import enrich_value_from_type

SIZE = 12.0
SPACE = 2.7  # ordinary word space at 12pt (~0.23em)
COLUMN = 20.0  # column gap


def _line(*cells: str, top: float = 100.0) -> list[PDFWord]:
    """Lays out cells on one line: words within a cell are space-separated,
    cells are column-separated. Each character is 6pt wide."""
    words: list[PDFWord] = []
    x = 50.0
    for cell in cells:
        for k, text in enumerate(cell.split()):
            if k:
                x += SPACE
            words.append(
                PDFWord(text=text, x0=x, x1=x + 6 * len(text), top=top, size=SIZE)
            )
            x += 6 * len(text)
        x += COLUMN
    return words


class TestGrouping:
    """Tests for line grouping and cell splitting."""

    def test_cells_split_on_column_gaps_only(self) -> None:
        (line,) = group_lines(_line("D2", "5mm Yellow", "C8", "1uF"))
        assert [c.text for c in split_cells(line)] == ["D2", "5mm Yellow", "C8", "1uF"]

    def test_nearby_footnote_line_is_separate(self) -> None:
        # The Abyss: a footnote sits 2.2pt above a parts row.
        words = _line("D2", "5mm Yellow", top=582.4) + _line(
            "16mm potentiometer", top=580.2
        )
        assert len(group_lines(words)) == 2

    def test_small_baseline_jitter_stays_on_one_line(self) -> None:
        words = [
            *_line("R8", "47K", top=582.44),
            PDFWord(text="C8", x0=300, x1=312, top=583.04, size=SIZE),
        ]
        assert len(group_lines(words)) == 1


class TestFindListings:
    """Tests for designator/value pairing."""

    def test_multi_column_row(self) -> None:
        words = _line(
            "R1", "1M", "R20", "3K3", "C1", "100nF", "IC1", "TL072", "INTENSITY", "B25K"
        )
        assert find_listings(words) == [
            Listing("R1", "1M"),
            Listing("R20", "3K3"),
            Listing("C1", "100nF"),
            Listing("IC1", "TL072"),
            Listing("INTENSITY", "B25K"),
        ]

    def test_multi_word_values_are_kept_whole(self) -> None:
        words = _line("C6", "1u MLCC", "D1", "3mm Red LED", "SW1", "SPDT (On/Off/On)")
        assert [listing.value for listing in find_listings(words)] == [
            "1u MLCC",
            "3mm Red LED",
            "SPDT (On/Off/On)",
        ]

    def test_annotation_cells_join_the_value(self) -> None:
        words = _line("C102", "10u", "(25V or higher)", "D2", "1N4148*")
        assert find_listings(words) == [
            Listing("C102", "10u (25V or higher)"),
            Listing("D2", "1N4148"),
        ]

    def test_column_headers_are_not_annotations(self) -> None:
        words = _line("C6", "1u MLCC", "DIODES")
        assert find_listings(words) == [Listing("C6", "1u MLCC")]

    @pytest.mark.parametrize("part", ["TL072", "BS170", "MPSA18", "J201", "FV1"])
    def test_part_numbers_shaped_like_designators_are_values(self, part: str) -> None:
        assert find_listings(_line("Q1", part)) == [Listing("Q1", part)]

    def test_unknown_control_names_found_by_value_shape(self) -> None:
        words = _line("SHIFT", "B25K") + _line("SUBS", "SPDT (On - Off - On)", top=120)
        assert {listing.ref for listing in find_listings(words)} == {"SHIFT", "SUBS"}

    def test_bare_designators(self) -> None:
        assert find_listings(_line("LED", "3mm Red LED", "RLED", "4K7")) == [
            Listing("LED", "3mm Red LED"),
            Listing("RLED", "4K7"),
        ]
        # 'LED' as a value, not a designator.
        assert find_listings(_line("D3", "LED")) == [Listing("D3", "LED")]

    def test_bare_led_after_a_value_starts_a_new_listing(self) -> None:
        # Pitch Witch: a bare 'LED' row shares a line with a capacitor.
        assert find_listings(_line("C12", "1nF", "LED", "Indicator LED")) == [
            Listing("C12", "1nF"),
            Listing("LED", "Indicator LED"),
        ]

    def test_bare_led_directly_after_a_designator_is_its_value(self) -> None:
        # Expresso: 'LED' is LED_A's value and 'Indicator LED' its TYPE.
        refs = [x.ref for x in find_listings(_line("LED_A", "LED", "Indicator LED"))]
        assert refs == ["LED_A"]

    def test_prose_is_one_cell_and_never_a_listing(self) -> None:
        words = _line("D8 and D9 are only needed when using Rotary clipping switch.")
        assert find_listings(words) == []

    def test_silkscreen_runs_are_not_listings(self) -> None:
        assert find_listings(_line("R13", "C4", "R12")) == []


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("22K*", "22K"),
        ("GE (see notes)", "GE"),
        ("AB38T-32.768KHZ Crystal", "AB38T-32.768KHZ"),
        ("2P4T Mini Rotary Switch", "2P4T Mini Rotary"),
        ("3mm Red LED", "3mm Red LED"),
    ],
)
def test_clean_value(raw: str, clean: str) -> None:
    assert clean_value(raw) == clean


@pytest.mark.parametrize(
    ("value", "type_text", "expected"),
    [
        ("GE", "Germanium diode, 1N34A", "1N34A"),
        ("ON/ON", "Toggle switch, SPDT 2-position (ON/ON)", "SPDT ON/ON"),
        ("10K", "Resistor, 1/4W", "10K"),
        ("1N4148", "Signal diode, DO-35", "1N4148"),
        ("RANDOMIZER", "Random LFO microcontroller", "RANDOMIZER"),
    ],
)
def test_enrich_value_from_type(value: str, type_text: str, expected: str) -> None:
    assert enrich_value_from_type(value, type_text) == expected


class TestCellRepairs:
    """Merged cells, function labels, wrapped values, and prose."""

    def test_name_merged_with_its_value_is_split(self) -> None:
        # Tab-aligned columns: 'BLEND' ends within a word space of 'B10K'.
        words = _line("R22", "10K", "BLEND B10K")
        assert find_listings(words) == [Listing("R22", "10K"), Listing("BLEND", "B10K")]

    def test_function_label_is_skipped(self) -> None:
        assert find_listings(_line("SW1 (Power) SPDT Toggle")) == [
            Listing("SW1", "SPDT Toggle")
        ]
        assert find_listings(_line("SW1", "(Voltage) SPDT Toggle (On/On)")) == [
            Listing("SW1", "SPDT Toggle (On/On)")
        ]

    def test_long_switch_spec_merged_cell(self) -> None:
        assert find_listings(_line("SW1 DPDT (On / Off / On)")) == [
            Listing("SW1", "DPDT (On / Off / On)")
        ]

    def test_wrapped_parenthetical_continues_the_value_above(self) -> None:
        above = _line("VOICE", "SPDT Toggle", top=100)
        value_x0 = next(w["x0"] for w in above if w["text"] == "SPDT")
        # Next line: '(On/On)' under VOICE's value; R21 sits in a column further right.
        below = [
            PDFWord(text="(On/On)", x0=value_x0, x1=value_x0 + 40, top=114, size=SIZE),
            PDFWord(text="R21", x0=300, x1=318, top=114, size=SIZE),
            PDFWord(text="33K", x0=340, x1=358, top=114, size=SIZE),
        ]
        assert find_listings([*above, *below]) == [
            Listing("VOICE", "SPDT Toggle (On/On)"),
            Listing("R21", "33K"),
        ]

    def test_prose_is_not_appended_to_a_value(self) -> None:
        words = _line("C7", "2u2", "Choose between Through-Hole or SMD")
        assert find_listings(words) == [Listing("C7", "2u2")]

    def test_section_headers_are_not_appended(self) -> None:
        assert find_listings(_line("R25", "4K7", "TRIM POTS")) == [
            Listing("R25", "4K7")
        ]
        assert find_listings(_line("R31", "100K", "DIP SWITCHES")) == [
            Listing("R31", "100K")
        ]

    def test_multiword_control_name(self) -> None:
        assert find_listings(_line("TONE BYPASS SWITCH", "DPDT ON/ON")) == [
            Listing("TONE BYPASS SWITCH", "DPDT ON/ON")
        ]

    def test_c_taper_value_is_not_a_designator(self) -> None:
        assert find_listings(_line("RATE", "C1M", "RES", "C1M")) == [
            Listing("RATE", "C1M"),
            Listing("RES", "C1M"),
        ]
