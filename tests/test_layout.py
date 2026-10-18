"""Unit tests for column-aware listing extraction in `src/bom_lib/layout.py`."""

import pytest

from src.bom_lib import PDFWord
from src.bom_lib.layout import (
    BomRow,
    Listing,
    bom_note_cells,
    clean_value,
    find_bom_rows,
    find_listings,
    footnote_marks,
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
        # A bare 'DIP2' would read as an IC package size (Frost Drive).
        ("DIP-5 Dip Switch", "DIP-5 Dip Switch"),
        ("DIP2 Dip Switch", "DIP2 Dip Switch"),
        ("AC128 (or equivalent)", "AC128"),  # Twin Face
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
        # Aion FX lists LED specs after 'LED,' and switch positions with hyphens.
        ("5mm", "LED, 5mm, red diffused", "5mm Red Diffused LED"),
        ("5mm green", "LED, 5mm, green diffused", "5mm Green Diffused LED"),
        ("100n", "MLCC capacitor, X7R", "100n MLCC"),
    ],
)
def test_enrich_value_from_type(value: str, type_text: str, expected: str) -> None:
    assert enrich_value_from_type(value, type_text) == expected


def test_switch_spec_is_not_replaced_by_a_part_number() -> None:
    # Aion FX Sentinel: 'VOLTAGE | DPDT slide | Slide switch, DPDT | E-Switch EG2207'.
    assert enrich_value_from_type(
        "DPDT slide", "Slide switch, DPDT", "E-Switch EG2207"
    ) == ("DPDT slide")


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
        assert find_listings(_line("R25", "33K", "PIN HEADERS")) == [
            Listing("R25", "33K")
        ]

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
        # Q-Tune's quantity column: a range is never valued by a bare count.
        assert find_listings(_line("D21 - D28", "8")) == []
        # The range lists all six pots (6 Band EQ), not 'POT1' valued '- 6 B50K'.
        assert find_listings(_line("POT1 - 6", "B50K")) == [Listing("POT1 - 6", "B50K")]

    def test_a_range_cell_is_not_split(self) -> None:
        # Dr Phybes: 'Q1 -Q4' is one range, not 'Q1' valued '-Q4 2N5952'.
        assert find_listings(_line("Q1 -Q4", "2N5952")) == [Listing("Q1 -Q4", "2N5952")]

    def test_a_spaced_designator_ends_a_value(self) -> None:
        # EA Tremolo: 'C5  470n  63V  SW 1  3PDT' does not run C5 into the switch.
        assert find_listings(_line("C5", "470n", "63V", "SW 1", "3PDT"))[0] == Listing(
            "C5", "470n 63V"
        )

    def test_range_in_a_listing_grid(self) -> None:
        # GuitarPCB SWAH: 'C9  1u  16V  D1-D2  1N4148' is two listings.
        assert find_listings(_line("C9", "1u", "16V", "D1-D2", "1N4148")) == [
            Listing("C9", "1u 16V"),
            Listing("D1-D2", "1N4148"),
        ]

    def test_title_case_control_beside_a_tapered_pot(self) -> None:
        # GuitarPCB Tone TwEQ: 'R11  10k  Volume  A100k'.
        assert find_listings(_line("R11", "10k", "Volume", "A100k")) == [
            Listing("R11", "10k"),
            Listing("VOLUME", "A100k"),
        ]

    def test_leading_see_text_markers(self) -> None:
        assert find_listings(_line("*R13", "1k8", "**D3", "1n4001")) == [
            Listing("R13", "1k8"),
            Listing("D3", "1n4001"),
        ]

    def test_lower_case_kilohms_name_a_control(self) -> None:
        assert find_listings(_line("VOL", "A100k")) == [Listing("VOL", "A100k")]

    def test_multiword_control_name(self) -> None:
        assert find_listings(_line("TONE BYPASS SWITCH", "DPDT ON/ON")) == [
            Listing("TONE BYPASS SWITCH", "DPDT ON/ON")
        ]

    def test_bare_toggle_positions_are_a_control_value(self) -> None:
        # Trojan Horse: 'SLA  ON/OFF/ON'; Minidrive: 'DPDT On-On-On' stays one value.
        assert find_listings(_line("SLA", "ON/OFF/ON")) == [Listing("SLA", "ON/OFF/ON")]
        assert find_listings(_line("SW1", "DPDT On-On-On")) == [
            Listing("SW1", "DPDT On-On-On")
        ]

    def test_value_stops_at_the_next_designator_column(self) -> None:
        # Emu Amp Simulator: a pin-header note sits in the IC column.
        words = [
            *_line("R1", "1M", "IC1", "TL072", top=100),
            *_line("R2", "1M", "IC2", "TL072", top=120),
            *_line("R3", "1M", "IC3", "TL072", top=140),
            *_line("R4", "1n", "Pinheader", top=160),  # note starts at the IC column
        ]
        assert Listing("R4", "1n") in find_listings(words)

    def test_listings_under_a_trim_pots_heading_are_trimmers(self) -> None:
        # CDXL Reissue: R23 and R20 sit in the column under 'TRIM POTS (3362P Type)'.
        words = [
            *_line("R16", "15K", "TRIM POTS (3362P Type)", top=100),
            *_line("R17", "470K", "R23", "10K", top=120),
        ]
        assert find_listings(words) == [
            Listing("R16", "15K"),
            Listing("R17", "470K"),
            Listing("R23", "10K TRIM"),
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

    def test_typed_part_table(self) -> None:
        # Aion FX: 'PART | VALUE | TYPE | NOTES'. Every row with a TYPE is a part,
        # whatever its label; a wrapped note continues its row; the page footer
        # (no TYPE) is not a row.
        cols = (50.0, 110.0, 200.0, 420.0)
        words = (
            self._row(100, *zip(("PART", "VALUE", "TYPE", "NOTES"), cols, strict=True))
            + self._row(
                120, ("R1", cols[0]), ("1M", cols[1]), ("Metal film resistor", cols[2])
            )
            + self._row(
                140,
                ("VOL.", cols[0]),
                ("100kA", cols[1]),
                ("16mm right-angle PCB mount pot", cols[2]),
                ("Original uses 50kA,", cols[3]),
            )
            + self._row(160, ("but 100k gives more range.", cols[3]))
            + self._row(
                180,
                ("FSW", cols[0]),
                ("3PDT", cols[1]),
                ("Stomp switch, 3PDT", cols[2]),
            )
            + self._row(
                200, ("ACHILLES AMP", cols[0]), ("OVERDRIVE", cols[1]), ("4", cols[3])
            )
        )
        rows = find_bom_rows(words)
        assert [(r.refs, r.value, r.type_text) for r in rows] == [
            ("R1", "1M", "Metal film resistor"),
            ("VOL.", "100kA", "16mm right-angle PCB mount pot"),
            ("FSW", "3PDT", "Stomp switch, 3PDT"),
        ]
        assert rows[1].notes == "Original uses 50kA, but 100k gives more range."
        assert [c.text for c in bom_note_cells(words)] == [
            "Original uses 50kA,",
            "but 100k gives more range.",
            "4",
        ]

    def test_grid_labels_markers_and_counts(self) -> None:
        # GuitarPCB 'Part | Value' grids: named controls, '*' markers on refs and
        # values, and a 'CLR x1' count.
        cols = (50.0, 120.0, 200.0, 270.0)
        words = (
            self._row(100, *zip(("Part", "Value", "Part", "Value"), cols, strict=True))
            + self._row(
                120,
                ("C5", cols[0]),
                ("*100p", cols[1]),
                ("VOL", cols[2]),
                ("A500K", cols[3]),
            )
            + self._row(
                140,
                ("*DRIVE", cols[0]),
                ("A100K", cols[1]),
                ("* CLR x1", cols[2]),
                ("1k8 - 4k7", cols[3]),
            )
        )
        rows = find_bom_rows(words)
        assert [(r.refs, r.value, r.quantity) for r in rows] == [
            ("C5", "100p", None),
            ("VOL", "A500K", None),
            ("DRIVE", "A100K", None),
            ("CLR", "1k8 - 4k7", 1),
        ]

    def test_two_tables_on_one_page_are_numbered_apart(self) -> None:
        cols = (50.0, 120.0)
        words = (
            self._row(100, ("Part", cols[0]), ("Value", cols[1]))
            + self._row(120, ("R1", cols[0]), ("1M", cols[1]))
            + self._row(200, ("Part", cols[0]), ("Value", cols[1]))
            + self._row(220, ("R1", cols[0]), ("1k", cols[1]))
        )
        assert [(r.refs, r.value, r.table) for r in find_bom_rows(words)] == [
            ("R1", "1M", 0),
            ("R1", "1k", 1),
        ]


def test_footnote_marks() -> None:
    # Aion FX legacy: 'R2  68k¹' sets the marker at about 0.6x the value's size.
    words = [
        PDFWord(text="R2", x0=100, x1=114, top=100.5, size=11.0),
        PDFWord(text="68k", x0=131, x1=148, top=100.5, size=11.0),
        PDFWord(text="1", x0=151, x1=155, top=100.0, size=6.4),
        PDFWord(text="C2", x0=263, x1=277, top=100.5, size=11.0),
        PDFWord(text="22", x0=294, x1=306, top=100.5, size=11.0),
    ]
    assert [w["text"] for w in footnote_marks(words)] == ["1"]
