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
