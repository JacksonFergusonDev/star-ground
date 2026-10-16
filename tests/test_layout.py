"""Unit tests for column-aware listing extraction in `src/bom_lib/layout.py`."""

import pytest

from src.bom_lib import PDFWord
from src.bom_lib.layout import (
    BomRow,
    Listing,
    clean_value,
    find_bom_rows,
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
        ("24LC32A *Included", "24LC32A"),  # Deflector: supplied with the PCB
        # Mini Heterodyne: a trailing link or remark.
        ("Mini 1P8T - https://www.pedalpcb.com/product/mini-1p8t/", "Mini 1P8T"),
        ("SPDT (On/On) - Short shaft toggle recommended", "SPDT (On/On)"),
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
        assert find_listings(_line("C12", "120p", "LEDs")) == [Listing("C12", "120p")]
        assert find_listings(_line("R43", "10K", "FOOTSWITCHES")) == [
            Listing("R43", "10K")
        ]
        assert find_listings(_line("R22", "1M", "TUBES")) == [Listing("R22", "1M")]

    def test_control_values_with_a_leading_qualifier(self) -> None:
        # Arkaim, Pro-Filter, Super Heterodyne Receiver.
        assert find_listings(_line("HEAVY", "Latching 3PDT Footswitch")) == [
            Listing("HEAVY", "Latching 3PDT Footswitch")
        ]
        assert find_listings(_line("SHIFT", "On/OFF/On DPDT Toggle")) == [
            Listing("SHIFT", "On/OFF/On DPDT Toggle")
        ]
        assert find_listings(_line("OSC-ROOT", "*DPDT On/On/On")) == [
            Listing("OSC-ROOT", "DPDT On/On/On")
        ]
        assert find_listings(
            _line("SW1", "SPDT Toggle (On/On)", "FREQ", "Dual B10K")
        ) == [
            Listing("SW1", "SPDT Toggle (On/On)"),
            Listing("FREQ", "Dual B10K"),
        ]
        # A name merged with its qualified value.
        assert find_listings(_line("BYPASS Latching 3PDT Footswitch")) == [
            Listing("BYPASS", "Latching 3PDT Footswitch")
        ]

    def test_optional_part_marker_is_not_part_of_the_name(self) -> None:
        # Fuchsia: 'TREBLE*' marks a control installed only in one build.
        assert find_listings(_line("C100", "100u", "TREBLE*", "A25K")) == [
            Listing("C100", "100u"),
            Listing("TREBLE", "A25K"),
        ]

    def test_dash_separates_a_name_from_its_value(self) -> None:
        # HAARP: 'DIRECTION - SPDT On/Off/On'.
        assert find_listings(_line("MODE - 1P8T Mini Rotary")) == [
            Listing("MODE", "1P8T Mini Rotary")
        ]

    def test_designator_in_parentheses_is_the_ref(self) -> None:
        # Muzzle: 'RANGE (SW1) - Mini Slide SPDT' and a labeled jack.
        assert find_listings(_line("RANGE (SW1) - Mini Slide SPDT")) == [
            Listing("SW1", "Mini Slide SPDT")
        ]
        assert find_listings(_line("SIDECHAIN (Key) - 1/4in Switching TRS")) == [
            Listing("SIDECHAIN", "1/4in Switching TRS")
        ]

    def test_short_and_slashed_control_names(self) -> None:
        # Blue Waffle's 'V  B500K'; Provocateur's 'BASS/MIDS  B100K'.
        assert find_listings(_line("R13", "1K5", "V", "B500K")) == [
            Listing("R13", "1K5"),
            Listing("V", "B500K"),
        ]
        assert find_listings(_line("R17", "1K", "BASS/MIDS", "B100K")) == [
            Listing("R17", "1K"),
            Listing("BASS/MIDS", "B100K"),
        ]

    def test_designator_without_a_value_at_the_end_of_a_listing_line(self) -> None:
        # SuperNuva: 'R16  10K  C12  100u  D1' lists D1 with no value.
        assert find_listings(_line("R16", "10K", "C12", "100u", "D1")) == [
            Listing("R16", "10K"),
            Listing("C12", "100u"),
            Listing("D1", ""),
        ]

    def test_dashed_ranges_are_not_name_and_value(self) -> None:
        assert find_listings(_line("D21 - D28", "8")) == []
        assert find_listings(_line("POT1 - 6", "B50K")) == []

    def test_multiword_control_name(self) -> None:
        assert find_listings(_line("TONE BYPASS SWITCH", "DPDT ON/ON")) == [
            Listing("TONE BYPASS SWITCH", "DPDT ON/ON")
        ]

    def test_c_taper_value_is_not_a_designator(self) -> None:
        assert find_listings(_line("RATE", "C1M", "RES", "C1M")) == [
            Listing("RATE", "C1M"),
            Listing("RES", "C1M"),
        ]


class TestTextBillOfMaterials:
    """Unruled 'Reference | Value | Quantity | Notes' tables (Sheepylove)."""

    @staticmethod
    def _row(top: float, *cells: tuple[str, float]) -> list[PDFWord]:
        """Words placed at explicit column positions, as in a tab-aligned table."""
        words: list[PDFWord] = []
        for text, x in cells:
            for word in text.split():
                words.append(
                    PDFWord(text=word, x0=x, x1=x + 6 * len(word), top=top, size=SIZE)
                )
                x += 6 * len(word) + SPACE
        return words

    def test_rows_are_read_by_header_column(self) -> None:
        cols = (50.0, 150.0, 220.0, 300.0)
        words = (
            self._row(
                100,
                *zip(("Reference", "Value", "Quantity", "Notes"), cols, strict=True),
            )
            + self._row(120, ("R10-R12,R18", cols[0]), ("47k", cols[1]), ("4", cols[2]))
            + self._row(
                140,
                ("D3,D4", cols[0]),
                ("Ge", cols[1]),
                ("2", cols[2]),
                ("Any Ge diodes will work.", cols[3]),
            )
            + self._row(160, ("Copyright: Sheepylove.com 2026-03-18", cols[0]))
        )
        rows = find_bom_rows(words)
        assert [(r.refs, r.value, r.quantity) for r in rows] == [
            ("R10-R12,R18", "47k", 4),
            ("D3,D4", "Ge", 2),
        ]
        assert isinstance(rows[0], BomRow)

    def test_side_by_side_centered_tables(self) -> None:
        # Q-Tune: two 'Value | Name | Qty' tables per line, cells centered under
        # their headers, spaced designator lists, and a marking after the value.
        header = self._row(
            100,
            ("Value", 67),
            ("Name", 175),
            ("Qty", 259),
            ("Value", 343),
            ("Name", 461),
            ("Qty", 549),
        )
        row = self._row(
            120,
            ("100n (104)", 53),
            ("C12, 15, 16", 160),
            ("3", 266),
            ("47R", 347),
            ("R11, R13", 450),
            ("2", 556),
        )
        rows = find_bom_rows(header + row)
        assert [(r.refs, r.value, r.quantity, r.marking) for r in rows] == [
            ("C12,15,16", "100n", 3, "104"),
            ("R11,R13", "47R", 2, ""),
        ]

    def test_no_header_no_rows(self) -> None:
        assert find_bom_rows(_line("R1", "10K", "C1", "100n")) == []
