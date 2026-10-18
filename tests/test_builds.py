"""Unit tests for build planning (variants and options) in `src/bom_lib/builds.py`."""

import pytest

from src.bom_lib import (
    BuildVariant,
    ParseResult,
    create_empty_inventory,
    create_empty_stats,
)
from src.bom_lib.builds import Entry, PageContext, plan_builds
from src.bom_lib.layout import Cell
from src.bom_lib.types import PDFWord


def _cell(text: str, x0: float) -> Cell:
    words, x = [], x0
    for token in text.split():
        words.append(PDFWord(text=token, x0=x, x1=x + 6 * len(token), top=0, size=12))
        x += 6 * len(token) + 3
    return Cell(tuple(words))


def _page(*lines: list[tuple[str, float]], text: str = "") -> PageContext:
    return PageContext([[_cell(t, x) for t, x in line] for line in lines], text)


def _names(plan) -> list[str]:
    return [v.name for v in plan.variants]


class TestGrids:
    """Side-by-side parts lists (Muffin Fuzz, FX5X)."""

    def test_repeated_designators_with_a_header_become_variants(self) -> None:
        page = _page(
            [("Green Russian", 40), ("Civil War", 280)],
            [("R1", 40), ("39K", 80), ("R1", 280), ("39K", 320)],
            [("R2", 40), ("100K", 80), ("R2", 280), ("82K", 320)],
        )
        entries = [
            Entry("R1", "39K", 0, line=1, x0=40),
            Entry("R1", "39K", 0, line=1, x0=280),
            Entry("R2", "100K", 0, line=2, x0=40),
            Entry("R2", "82K", 0, line=2, x0=280),
        ]
        plan = plan_builds(entries, [page])
        assert _names(plan) == ["Green Russian", "Civil War"]
        assert [e.value for e in plan.variants[1].entries] == ["39K", "82K"]
        assert plan.common == ()

    def test_grid_without_a_header_is_named_by_its_first_row(self) -> None:
        # Frost Drive: an alternate-pot table under a sentence ending '... accordingly.'
        page = _page(
            [("accordingly.", 40)],
            [("MID", 40), ("C10K Dual", 80), ("MID", 280), ("C100K Dual", 320)],
            [("R4", 40), ("1K", 80), ("R4", 280), ("10K", 320)],
        )
        entries = [
            Entry("MID", "C10K Dual", 0, line=1, x0=40),
            Entry("MID", "C100K Dual", 0, line=1, x0=280),
            Entry("R4", "1K", 0, line=2, x0=40),
            Entry("R4", "10K", 0, line=2, x0=280),
        ]
        plan = plan_builds(entries, [page])
        assert _names(plan) == ["MID C10K Dual", "MID C100K Dual"]

    def test_see_below_placeholders_are_replaced_by_the_grid(self) -> None:
        page = _page(
            [("R1", 40), ("1M", 80), ("IC1", 280), ("SEE BELOW", 320)],
            [("OD+", 40), ("SUPRA", 280)],
            [("IC1", 40), ("MC1458", 80), ("IC1", 280), ("LF353", 320)],
        )
        entries = [
            Entry("R1", "1M", 0, line=0, x0=40),
            Entry("IC1", "SEE BELOW", 0, line=0, x0=280),
            Entry("IC1", "MC1458", 0, line=2, x0=40),
            Entry("IC1", "LF353", 0, line=2, x0=280),
        ]
        plan = plan_builds(entries, [page])
        assert _names(plan) == ["OD+", "SUPRA"]
        assert [e.ref for e in plan.common] == ["R1"]


def test_overlapping_tables_on_separate_pages_are_variants() -> None:
    refs = [f"R{i}" for i in range(1, 11)]
    ge = [Entry(r, "1K", 0, table=0) for r in refs]
    si = [Entry(r, "2K", 1, table=1) for r in refs]
    pages = [
        _page(text="Antithesis Fuzz\n(Germanium Version)\nLOCATION VALUE"),
        _page(text="Antithesis Fuzz\n(Silicon Version)\nLOCATION VALUE"),
    ]
    plan = plan_builds(ge + si, pages)
    assert _names(plan) == ["Germanium Version", "Silicon Version"]


def test_continued_tables_are_not_variants() -> None:
    first = [Entry(f"R{i}", "1K", 0, table=0) for i in range(1, 11)]
    second = [Entry(f"C{i}", "1u", 1, table=1) for i in range(1, 11)]
    plan = plan_builds(first + second, [_page(), _page()])
    assert plan.variants == ()


def test_modification_block_becomes_a_variant() -> None:
    page = _page(
        [("Q1", 40), ("2N3904", 80)],
        [("CLEFT MODIFICATION", 280)],
        [("Q1", 280), ("2N5089", 320)],
        [("C16", 280), ("Not Populated", 320)],
    )
    entries = [
        Entry("Q1", "2N3904", 0, line=0, x0=40),
        Entry("C16", "470p", 0, line=0, x0=160),
        Entry("Q1", "2N5089", 0, line=2, x0=280),
        Entry("C16", "Not Populated", 0, line=3, x0=280),
    ]
    plan = plan_builds(entries, [page])
    assert _names(plan) == ["Stock", "Cleft Modification"]
    stock, mod = plan.variants
    assert {e.value for e in stock.entries} == {"2N3904", "470p"}
    assert {e.value for e in mod.entries} == {"2N5089", "Not Populated"}


def test_a_build_table_beside_the_list() -> None:
    # Five Cats Octavia: 'To build the vintage Octavia, with no tone pot' over
    # a table sharing lines with the parts list (C10 on both), split by OCR
    # into a cell per word; 'Volume' is added, 'Omit - ...' leaves C10 out.
    page = _page(
        [
            ("C9", 180),
            ("22uF", 224),
            ("To", 411),
            ("build", 422),
            ("the", 444),
            ("vintage", 458),
            ("Octavia,", 487),
            ("with", 520),
            ("no", 538),
        ],
        [("C10", 180), ("10nF", 224), ("C10", 394), ("Omit", 438)],
        [("C11", 180), ("100nF", 224), ("Volume", 394), ("A1M", 438)],
    )
    entries = [
        Entry("C9", "22uF", 0, line=0, x0=180),
        Entry("C10", "10nF", 0, line=1, x0=180),
        Entry("C10", "Omit - jumper pads 2+3", 0, line=1, x0=394),
        Entry("C11", "100nF", 0, line=2, x0=180),
        Entry("VOLUME", "A1M", 0, line=2, x0=394),
    ]
    plan = plan_builds(entries, [page])
    assert _names(plan) == ["Stock", "Vintage Octavia"]
    stock, vintage = plan.variants
    assert {e.ref for e in stock.entries} == {"C10"}
    assert {(e.ref, e.not_populated) for e in vintage.entries} == {
        ("C10", True),
        ("VOLUME", False),
    }


def test_either_or_note_makes_variants() -> None:
    page = _page(
        text="NOTE: PCB is designed for either Nature control, or Treble + Bass"
        " controls, do not install all three."
    )
    entries = [
        Entry("R1", "1M", 0, line=0, x0=40),
        Entry("TREBLE", "A25K", 0, line=1, x0=560),
        Entry("BASS", "C10K", 0, line=2, x0=560),
        Entry("NATURE", "W20K", 0, line=3, x0=560),
    ]
    plan = plan_builds(entries, [page])
    assert _names(plan) == ["Nature", "Treble + Bass"]
    assert [e.ref for e in plan.build_entries(plan.variants[1])] == [
        "R1",
        "TREBLE",
        "BASS",
    ]


def test_either_or_note_naming_unlisted_parts_is_ignored() -> None:
    page = _page(text="Use either the stock control, or the mod controls.")
    plan = plan_builds([Entry("R1", "1M", 0, line=0, x0=40)], [page])
    assert plan.variants == ()


class TestOptions:
    """Interchangeable choices: the first is used by default."""

    def test_or_between_two_listings_of_one_designator(self) -> None:
        page = _page(
            [("SW1", 280), ("2P4T Mini Rotary", 320)],
            [("OR", 280)],
            [("SW1", 280), ("SPDT ON/OFF/ON", 320)],
        )
        entries = [
            Entry("SW1", "2P4T Mini Rotary", 0, line=0, x0=280),
            Entry("SW1", "SPDT ON/OFF/ON", 0, line=2, x0=280),
        ]
        plan = plan_builds(entries, [page])
        (option,) = plan.options
        assert [name for name, _ in option.choices] == [
            "2P4T Mini Rotary",
            "SPDT ON/OFF/ON",
        ]
        assert [e.value for e in plan.build_entries()] == ["2P4T Mini Rotary"]

    def test_or_row_in_a_ruled_table(self) -> None:
        # Shindig: a bridge rectifier OR four discrete diodes.
        entries = [
            Entry("D4", "1N4148", 0, table=0),
            Entry("D10", "DI1510", 0, table=0),
            Entry("D10a", "1N4007", 0, table=0, follows_or=True),
            Entry("D10b", "1N4007", 0, table=0),
            Entry("DRIVE", "A1M", 0, table=0),
        ]
        plan = plan_builds(entries, [_page()])
        (option,) = plan.options
        assert option.group == "D10"
        assert [[e.ref for e in c] for _, c in option.choices] == [
            ["D10"],
            ["D10a", "D10b"],
        ]
        assert [e.ref for e in plan.build_entries()] == ["D4", "DRIVE", "D10"]

    def test_choose_between_with_sub_headings(self) -> None:
        page = _page(
            [("TRANSISTORS", 560)],
            [("Choose between Through-Hole or SMD", 560)],
            [("Through-Hole", 560)],
            [("Q1", 560), ("2N5485", 600)],
            [("SMD", 560)],
            [("Q7", 560), ("MMBF5485", 600)],
        )
        entries = [
            Entry("Q1", "2N5485", 0, line=3, x0=560),
            Entry("Q7", "MMBF5485", 0, line=5, x0=560),
        ]
        plan = plan_builds(entries, [page])
        (option,) = plan.options
        assert option.group == "Transistors"
        assert [name for name, _ in option.choices] == ["Through-Hole", "SMD"]
        assert [e.ref for e in plan.build_entries()] == ["Q1"]


def test_plain_document_is_one_build() -> None:
    page = _page([("R1", 40), ("10K", 80), ("C1", 280), ("100n", 320)])
    entries = [
        Entry("R1", "10K", 0, line=0, x0=40),
        Entry("C1", "100n", 0, line=0, x0=280),
    ]
    plan = plan_builds(entries, [page])
    assert plan.variants == ()
    assert plan.options == ()
    assert len(plan.common) == 2


class TestParseResultSelect:
    """A multi-circuit result has no default build."""

    def _result(self) -> ParseResult:
        stock, mod = create_empty_inventory(), create_empty_inventory()
        return ParseResult(
            inventory=create_empty_inventory(),
            stats=create_empty_stats(),
            variants=(
                BuildVariant("Stock", stock, 52),
                BuildVariant("Cleft Mod", mod, 51),
            ),
        )

    def test_requires_choice_until_selected(self) -> None:
        result = self._result()
        assert result.requires_choice
        assert result.variant_names == ("Stock", "Cleft Mod")
        chosen = result.select("Cleft Mod")
        assert not chosen.requires_choice
        assert chosen.stats["parts_found"] == 51

    def test_unknown_build_raises(self) -> None:
        with pytest.raises(KeyError):
            self._result().select("Ram's Head")


def test_choose_between_row_offers_the_rows_beside_it() -> None:
    # Spelunker: 'GAIN_TRIM 250K' / 'Choose between internal trimpot or external
    # potentiometer, do not install both' / 'GAIN C250K'.
    entries = [
        Entry("R1", "10K", 0, table=0),
        Entry("GAIN_TRIM", "250K", 0, table=0),
        Entry("GAIN", "C250K", 0, table=0, follows_or=True),
    ]
    plan = plan_builds(entries, [])
    assert [e.ref for e in plan.common] == ["R1"]
    (option,) = plan.options
    assert [[e.ref for e in c] for _, c in option.choices] == [["GAIN_TRIM"], ["GAIN"]]


def test_repeated_lists_without_margin_names_are_one_build() -> None:
    # Two boards (or a schematic beside the list) repeat labels too; with no
    # name beside each repeat they are not builds, and overlapping spans leave
    # a repeat empty (Aion FX Halo legacy) without failing.
    entries = [
        Entry(ref if k == 0 else f"{ref} ({k + 1})", value, 0, line=line)
        for k, line in ((0, 5), (1, 2))
        for ref, value in [(f"R{n}", "10k") for n in range(1, 10)]
    ]
    plan = plan_builds(entries, [_page([("R1", 40)] * 1)])
    assert plan.variants == ()


def test_repeated_tables_named_by_their_headings_are_builds() -> None:
    # Aion FX legacy Plasma: 'Overdriver Parts List' and 'Power Boost Parts
    # List' each head a complete parts table (ruled, so the rows carry no line).
    def heading(text: str) -> list[Cell]:
        return [
            Cell(tuple(PDFWord(text=t, x0=40, x1=90, top=0, size=14) for t in [text]))
        ]

    refs = [f"R{n}" for n in range(1, 11)]
    page = PageContext(
        [heading("Parts List"), [_cell("Capacitors", 40)]]
        + [[_cell(r, 40), _cell("1k", 80)] for r in refs],
        "",
    )
    pages = [
        PageContext([heading("Overdriver Parts List"), *page.lines[1:]], ""),
        PageContext([heading("Power Boost Parts List"), *page.lines[1:]], ""),
    ]
    entries = [
        Entry(r if p == 0 else f"{r} (2)", "1k", p, table=p)
        for p in (0, 1)
        for r in refs
    ]
    assert _names(plan_builds(entries, pages)) == ["Overdriver", "Power Boost"]
