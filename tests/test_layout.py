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
        assert split_cells(line) == ["D2", "5mm Yellow", "C8", "1uF"]

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
