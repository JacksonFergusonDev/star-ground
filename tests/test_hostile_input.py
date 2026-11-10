"""Long, hostile input is read in linear time.

Each case once took several seconds or minutes, because a regex retried a
scan from every position (or a check compared every line with every other).
A pasted list may be 200,000 characters and an upload far more, and a reading
has no time limit, so each could hold a server core.

A test times the work on an input and on one four times as large
(`_scales_linearly`), not against a bound in seconds: the suite runs on every
core at once, often beside other work, and a busy machine slows both inputs
alike. Where only a bound can help (a page drawn with thousands of ruled
lines, a page 200 inches square, a stream that inflates to gigabytes), the
bound is tested instead, by what the reader skips or the memory it takes.
"""

import gc
import io
import math
import time
import tracemalloc
import zlib
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pdfplumber
import pdfplumber.page
import pdfplumber.table
import pytest
from pdfminer.pdftypes import PDFStream
from pdfplumber.page import Page
from PIL import Image

from star_ground.bom_lib import FileUpload, builds, classifier, locations, ocr
from star_ground.bom_lib.boards import board_marks
from star_ground.bom_lib.builds import PageContext, plan_builds
from star_ground.bom_lib.choices import choose_value
from star_ground.bom_lib.enums import ComponentCategory, InputMethod
from star_ground.bom_lib.errors import CrowdedPageError, ErrorKind, HeavyPageError
from star_ground.bom_lib.ocr import OcrBox
from star_ground.bom_lib.pdf import bom_table, scan
from star_ground.bom_lib.pdf import document as pdf_document
from star_ground.bom_lib.pdf.cells import CONTROL_VALUE
from star_ground.bom_lib.pdf.content import content_bytes
from star_ground.bom_lib.pdf.geometry import Cell, Listing, page_lines
from star_ground.bom_lib.pdf.page import PDFPageExtraction
from star_ground.bom_lib.pdf.page_checks import (
    footnote_marks,
    marked_labels,
    valueless_refs,
)
from star_ground.bom_lib.pdf.scan import _description_end, find_listings
from star_ground.bom_lib.strategies.context import BOMParserContext
from star_ground.bom_lib.strategies.manual import parse_with_verification
from star_ground.bom_lib.types import (
    ComponentKey,
    Inventory,
    PDFWord,
    ReadingStats,
)
from star_ground.bom_lib.verification import check_footnotes
from tests.pdfs import assemble, stream, typeset

# A scaling check times the work on an input and on one `_GROWTH` times as
# large. Linear work takes about 4 times as long, and n log n about 5; the
# quadratic work each test guards against took about 16 times, so the check
# allows 10. Each input's size is one the quadratic work took a second or
# two on, so a regression fails quickly.
_GROWTH = 4
_MAX_SLOWDOWN = 10
# Each timing runs the work until the larger input's runs take about this
# long, so the timer's resolution is small beside it.
_MIN_TIMED_S = 0.01
_SAMPLES = 5


def _seconds(work: Callable[[], object]) -> float:
    """The processor time the work took, without the garbage collector.

    Processor time, not time on the clock: the suite runs on every core, often
    beside other work, and time spent waiting for a core isn't the work's. A
    test worker holds every corpus reading it has made, and the collector
    walks them all whenever the work allocates enough: under the full suite
    that took over ten times as long as the work itself.
    """
    gc.disable()
    try:
        start = time.thread_time()
        work()
        return time.thread_time() - start
    finally:
        gc.enable()


def _listings(words: list[PDFWord]) -> list[Listing]:
    """The listings on a page of words."""
    return find_listings(page_lines(words))


def _scales_linearly(work: Callable[[int], Callable[[], object]], size: int) -> None:
    """Asserts the work grows about linearly with its input.

    Both inputs are built first. Each timing runs the larger input's work
    some number of times and the smaller's `_GROWTH` times as often, so both
    take about as long and a busy machine slows them alike. The two are
    timed in turn, `_SAMPLES` times, and each one's fastest timing is kept.

    Args:
        work: Builds the input of a given size and returns the work on it.
        size: The larger input's size; the smaller is a quarter of it.
    """
    small, large = work(size // _GROWTH), work(size)
    small()  # Once to warm up, then once to see how often to run each.
    repeat = math.ceil(_MIN_TIMED_S / max(_GROWTH * _seconds(small), 1e-6))

    def runs(once: Callable[[], object], times: int) -> Callable[[], None]:
        def run() -> None:
            for _ in range(times):
                once()

        return run

    small_s = large_s = math.inf
    for _ in range(_SAMPLES):
        small_s = min(small_s, _seconds(runs(small, _GROWTH * repeat)) / _GROWTH)
        large_s = min(large_s, _seconds(runs(large, repeat)))
    assert large_s < _MAX_SLOWDOWN * small_s, (
        f"{size // _GROWTH:,} took {small_s / repeat:.5f} s and {size:,} took "
        f"{large_s / repeat:.5f} s"
    )


def test_pasted_line_with_a_long_run_of_spaces() -> None:
    # 15.6 s for 40,000 spaces before; quadrupled per doubling of the spaces.
    _scales_linearly(
        lambda n: partial(parse_with_verification, ["R1 x" + " " * n + "x"]), 16_000
    )


def test_pasted_line_is_still_read() -> None:
    result = parse_with_verification(["R1, 10k ", "C1\t100n"])
    assert {k.value for k in result.inventory} == {"10k", "100n"}
    assert result.stats.residuals == []


def test_pasted_value_of_many_slashes() -> None:
    # 14.2 s for 20,000 before, through the paste reader to `choose_value`.
    process = BOMParserContext().process
    _scales_linearly(
        lambda n: partial(
            process, InputMethod.PASTE_TEXT, "R1 " + "a/" * n + " 1", "s"
        ),
        8000,
    )


@pytest.mark.parametrize(
    ("value", "size"),
    [
        # Each with how long its smaller size took before.
        pytest.param(lambda n: "a/" * n + " 1", 8000, id="slashes"),  # 14 s
        pytest.param(lambda n: "1" * n + "!", 10_000, id="digits"),  # 30 s
        pytest.param(lambda n: "a" + " " * n + "b", 32_000, id="spaces"),  # 4 s
        pytest.param(
            lambda n: "(or a" + " " * n + "b", 16_000, id="open-alternative"
        ),  # 11 s
        pytest.param(lambda n: "(a" + " " * n + "b", 16_000, id="open-will-also-work"),
    ],
)
def test_choose_value_on_long_values(value: Callable[[int], str], size: int) -> None:
    _scales_linearly(lambda n: partial(choose_value, "R1", value(n)), size)


def test_choose_value_still_reads_choices() -> None:
    assert choose_value("R1", "1M to 2M2").value == "1M"
    assert choose_value("R1", "4u7 or 5uF electro").value == "4u7 electro"
    assert choose_value("R1", "270nF/220nF").value == "270nF"
    assert choose_value("R1", "51pF (or 47pF) MLCC").value == "51pF MLCC"
    assert choose_value("IC1", "4558 (TL072 will also work)").value == "4558"
    assert choose_value("D1", "Silicon diode (1N456, 1N4148, etc)").value == (
        "Silicon diode"
    )


def test_many_dip_sizes_in_one_value() -> None:
    # 6.3 s before: each DIP size rescanned the rest for 'SWITCH'.
    _scales_linearly(
        lambda n: partial(classifier.categorize_part, "FOO", "DIP1 " * n), 12_000
    )
    value = "DIP1 " * 20000
    assert classifier.DIP_SWITCH.search(value) is None
    assert classifier.DIP_SWITCH.search(value + "SWITCH") is not None
    assert classifier.DIP_SWITCH.search("X DIP8 8 POSITION SWITCH") is not None
    assert classifier.DIP_SWITCH.search("DIP8\nSWITCH") is None


def test_many_either_notes_on_a_page() -> None:
    # 9.2 s before: each 'either' scanned the rest of the page for 'control'.
    _scales_linearly(
        lambda n: partial(
            plan_builds, [], [PageContext([], "either " * n + "x control or y")]
        ),
        7000,
    )
    assert builds._EITHER_OR.search("either Nature control, or Treble + Bass controls")


def test_many_starred_lines() -> None:
    # 5.5 s before: every '*' heading matched against every line.
    _scales_linearly(
        lambda n: partial(
            check_footnotes, ["\n".join(f"*a{i}" for i in range(n))], ReadingStats()
        ),
        2400,
    )


def test_footnote_is_still_found() -> None:
    stats = ReadingStats()
    text = (
        "OSC-ROOT *DPDT On/On/On\nR1 10k\n*DPDT On/On/On\n"
        "This switch MUST be a Type-2 DPDT On/On/On toggle switch."
    )
    check_footnotes([text], stats)
    assert [w.message for w in stats.warnings] == [
        "OSC-ROOT (DPDT On/On/On) has a note: This switch MUST be a Type-2 "
        "DPDT On/On/On toggle switch."
    ]


def test_control_value_on_a_long_number() -> None:
    # 4.7 s per match of 40,000 digits before, and a cell is matched several times.
    _scales_linearly(lambda n: partial(CONTROL_VALUE.match, "1" * n), 24_000)
    assert CONTROL_VALUE.match('1/4" Switching TRS') is not None


def test_unclosed_aside_after_many_parentheses() -> None:
    # 3.3 s before: each '(' rescanned the rest of the cell for ')'.
    cell = "(" * 40000 + ")"
    # Two C-level scans of the cell now, which take microseconds: too little
    # to compare two sizes by, and a million times inside this bound.
    assert _seconds(partial(bom_table._without_unclosed_aside, cell)) < 1.0
    assert bom_table._without_unclosed_aside(cell) == cell
    assert bom_table._without_unclosed_aside("1N5226 (1N5") == "1N5226"
    assert bom_table._without_unclosed_aside("100n (104) (low") == "100n (104)"


def test_key_quantity_cache_is_bounded() -> None:
    # Keyed by values builders send: unbounded, it grows for the server's life.
    assert classifier.key_quantity.cache_info().maxsize is not None
    key = ComponentKey(ComponentCategory.RESISTORS, "10k")
    assert classifier.key_quantity(key) == classifier.key_quantity(key)


def _word(text: str, x0: float, top: float, size: float = 10.0) -> PDFWord:
    return PDFWord(text=text, x0=x0, x1=x0 + 0.5 * size * len(text), top=top, size=size)


def _footnoted_values(rows: int) -> list[PDFWord]:
    """Values in 12 pt, each with a 6 pt footnote mark: '10k¹'."""
    words = []
    for i in range(rows):
        x, top = 10 + (i % 50) * 30, 10 + (i // 50) * 14
        words += [_word("10k", x, top, 12.0), _word("1", x + 19, top + 6, 6.0)]
    return words


def _small_numbers(count: int) -> list[PDFWord]:
    return [
        _word("1", 10 + (i % 100) * 6, 10 + (i // 100) * 8, 6.0) for i in range(count)
    ]


def test_page_of_many_small_numbers() -> None:
    # 22.7 s for 20,000 before: every number was compared with every word.
    _scales_linearly(lambda n: partial(footnote_marks, _small_numbers(n)), 6000)


def test_page_of_many_footnote_marks() -> None:
    # 6.2 s before (`marked_labels`), and `footnote_marks` was quadratic too.
    def label(count: int) -> Callable[[], object]:
        words = _footnoted_values(count)
        return lambda: marked_labels(words, footnote_marks(words))

    _scales_linearly(label, 4000)
    words = _footnoted_values(8000)
    marks = footnote_marks(words)
    assert len(marks) == 8000
    assert all(m["text"] == "1" for m in marks)


def _pdf(content: str, width: float = 612, height: float = 792) -> bytes:
    """A one-page PDF drawing the content stream in Helvetica.

    Args:
        content: The page's content stream.
        width: The page's width, in points (US Letter's by default).
        height: The page's height, in points.
    """
    return assemble(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] ".encode()
        + b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        stream(content.encode("latin-1")),
    )


def _parsed(content: str) -> Page:
    """The page of a one-page PDF, parsed: parsing it isn't what's timed."""
    page = pdfplumber.open(io.BytesIO(_pdf(content))).pages[0]
    assert page.chars is not None
    return page


def _marked_values(count: int) -> str:
    """A content stream of `count` 4pt '10k's, each with a 2pt '1'."""
    text = []
    for i in range(count):
        x, y = 10 + (i % 40) * 15, 780 - (i // 40) * 6
        text.append(f"BT /F1 4 Tf {x} {y} Td (10k) Tj /F1 2 Tf 8 1 Td (1) Tj ET")
    return " ".join(text)


def _without_marks(page: Page) -> Page:
    """The page without the footnote marks found in its words."""
    marks = footnote_marks(pdf_document._page_words(page))
    return pdf_document._without_marks(page, marks)


def test_dropping_many_footnote_marks_from_a_page() -> None:
    # 6.5 s for 5,000 before: every character was compared with every mark.
    def drop(count: int) -> Callable[[], object]:
        page = _parsed(_marked_values(count))
        return lambda: _without_marks(page).chars

    _scales_linearly(drop, 3000)
    chars = _without_marks(_parsed(_marked_values(5000))).chars
    assert "".join(c["text"] for c in chars) == "10k" * 5000


def _valueless_refs_alone(
    words: Callable[[int], list[PDFWord]],
) -> Callable[[int], Callable[[], list[str]]]:
    """`valueless_refs` on a page of a size, its listings found beforehand.

    Finding listings isn't what these tests time, and it is most of the work.
    """

    def work(size: int) -> Callable[[], list[str]]:
        page = PDFPageExtraction(tables=[], text="", words=words(size))
        assert page.listings is not None  # Found once, and kept on the page.
        return partial(valueless_refs, page)

    return work


def _two_columns(count: int) -> list[PDFWord]:
    words = []
    for i in range(count):
        x, top = 10 + (i % 2) * 60, 10 + (i // 2) * 12
        words += [_word(f"R{i % 999 + 1}", x, top), _word("10k", x + 25, top)]
    return words


def test_designator_column_of_a_long_page() -> None:
    # 6.5 s for 8,000 before: each cell was compared with every listing and
    # every word.
    work = _valueless_refs_alone(_two_columns)
    _scales_linearly(work, 4000)
    assert work(8000)() == []


def _half_valueless(count: int) -> list[PDFWord]:
    words = []
    for i in range(count):
        words.append(_word(f"R{i % 900 + 1}", 10, 10 + i * 12))
        if i % 2:
            words.append(_word("10k", 60, 10 + i * 12))
    return words


def test_valueless_designators_of_a_long_column() -> None:
    # 3.6 s for 8,000 before.
    work = _valueless_refs_alone(_half_valueless)
    _scales_linearly(work, 4000)
    refs = work(8000)()
    assert len(refs) == 4000
    assert set(refs) == {f"R{n}" for n in range(1, 901, 2)}


def _typed_listings(count: int) -> list[PDFWord]:
    words = []
    for i in range(count):
        x, top = 10 + (i % 10) * 120, 10 + (i // 10) * 12
        words += [
            _word(f"R{i % 999 + 1}", x, top),
            _word("10k", x + 25, top),
            _word("Resistor", x + 55, top),
        ]
    return words


def test_long_page_of_typed_listings() -> None:
    # 6.6 s for 4,000 before: each typed cell re-typed every cell on the page.
    _scales_linearly(
        lambda n: partial(scan._type_columns, page_lines(_typed_listings(n))), 2000
    )
    words = _typed_listings(4000)
    columns = scan._type_columns(page_lines(words))
    assert columns == {65 + 120 * k for k in range(10)}
    listings = _listings(words)
    assert len(listings) == 4000
    assert {lst.value for lst in listings} == {"10k"}


def _pots(count: int) -> list[Cell]:
    return [Cell((_word("POT", 10 + 20 * i, 10),)) for i in range(count)]


def test_long_part_description() -> None:
    # 82 s for 20,000 before: each step rebuilt the texts of the whole line.
    _scales_linearly(
        lambda n: partial(_description_end, _pots(n), 0, frozenset()), 3000
    )
    assert _description_end(_pots(20_000), 0, frozenset()) == 20_000


def _dashed_listings(count: int) -> list[PDFWord]:
    """One line of 'R1 - 10k R2 - 10k …', set with word spaces: one cell."""
    words: list[PDFWord] = []
    x = 10.0
    for i in range(count):
        for text in (f"{'RCDQ'[i // 999]}{i % 999 + 1}", "-", "10k"):
            words.append(_word(text, x, 100))
            x = words[-1]["x1"] + 2.5
    return words


def test_many_dashed_listings_on_a_line() -> None:
    # 2.2 s for 800 before: each listing searched the whole line for the word
    # after it (`_dash_after`), and split the rest of its cell again.
    _scales_linearly(lambda n: partial(_listings, _dashed_listings(n)), 800)


def test_many_dashed_listings_in_one_cell() -> None:
    # A RecursionError, reported as a bug: each listing recursed into the rest.
    words = _dashed_listings(3000)
    cells = scan._dash_listing(Cell(tuple(words)))
    assert cells is not None
    assert [c.text for c in cells[:4]] == ["R1", "10k", "R2", "10k"]
    assert len(cells) == 6000
    listings = _listings(words)
    assert len(listings) == 3000
    assert all(lst.dashed and lst.value == "10k" for lst in listings)


@pytest.mark.parametrize(
    ("text", "cells"),
    [
        # Tonepad MAS Distortion; a glued dash only starts a cell's first listing.
        (
            "R7 - 500k Rev. Log. pot. C7 - 0.001 µF Q1- 2N3904 D1 - D5 - Silicon",
            [
                "R7",
                "500k Rev. Log. pot.",
                "C7",
                "0.001 µF Q1- 2N3904",
                "D1-D5",
                "Silicon",
            ],
        ),
        (
            "Q1- 2N3904 C7 - 10n R2, R3 - 4k7 A B E",
            ["Q1", "2N3904", "C7", "10n R2,", "R3", "4k7"],
        ),
        # A tail that is no listing stays one cell.
        ("R1, R2 - 10k R3 - D1 - D5 x", ["R1 R2", "10k", "R3-D1", "D5 x"]),
        ("POT1 - 6", None),
    ],
)
def test_dashed_listings_in_one_cell_are_split_as_before(
    text: str, cells: list[str] | None
) -> None:
    words = [_word(t, 10 + 40 * i, 100) for i, t in enumerate(text.split())]
    split = scan._dash_listing(Cell(tuple(words)))
    assert (None if split is None else [c.text for c in split]) == cells


def _mentions(count: int) -> PDFPageExtraction:
    """A line of 'R1, R1, … 10k', mentioning R1 `count` times."""
    words = [_word("R1,", 10 + 25 * i, 100) for i in range(count)]
    words.append(_word("10k", 10 + 25 * count, 100))
    return PDFPageExtraction(tables=[], text="", words=words)


def test_designator_printed_many_times_on_a_line() -> None:
    # 51 s for 8,000 before: each mention searched the rest of the line for
    # its value.
    inventory = Inventory()
    inventory.add_part("s", ComponentKey(ComponentCategory.RESISTORS, "10k"), "R1", 1)
    _scales_linearly(
        lambda n: partial(locations.locate, [_mentions(n)], inventory), 1600
    )
    page = _mentions(8000)
    located = locations.locate([page], inventory)
    assert located["R1"].x1 == page.words[-1]["x1"]


def test_number_cache_is_bounded() -> None:
    # Keyed by words from uploaded documents: unbounded, it grows for the
    # server's life.
    assert locations._number.cache_info().maxsize is not None


def _tiny_ones(count: int) -> str:
    """A content stream of `count` 2pt '1's, each in its own place."""
    return " ".join(
        f"BT /F1 2 Tf {10 + (i % 230) * 2.5:.1f} {780 - (i // 230) * 2.5:.1f} Td"
        " (1) Tj ET"
        for i in range(count)
    )


def test_page_of_many_characters_drops_overprint_quickly() -> None:
    # 3.2 s for 12,000 before: pdfplumber's `dedupe_chars` sorts by `list.index`.
    def drop(count: int) -> Callable[[], object]:
        page = _parsed(_tiny_ones(count))
        return lambda: pdf_document._without_overprint(page).chars

    _scales_linearly(drop, 10_000)
    page = _parsed(_tiny_ones(12_000))
    assert len(pdf_document._without_overprint(page).chars) == 12_000


def test_overprint_keeps_what_pdfplumber_keeps(tmp_path: Path) -> None:
    # Labels printed twice a hair apart (Tonepad's artwork), copies 1pt apart
    # and more, another glyph in the same place, and a smaller copy.
    text = []
    for i, (dx, dy) in enumerate([(0.3, 0.2), (0.9, 0), (1.5, 0), (0, 1.2)]):
        x = 50 + 130 * i
        text.append(f"BT /F1 10 Tf {x} 700 Td (R7 THRESHLD) Tj ET")
        text.append(f"BT /F1 10 Tf {x + dx} {700 + dy} Td (R7 THRESHLD) Tj ET")
    text.append("BT /F1 10 Tf 50 600 Td (R7) Tj ET BT /F1 10 Tf 50 600 Td (C7) Tj ET")
    text.append("BT /F1 10 Tf 50 500 Td (R7) Tj ET BT /F1 9 Tf 50 500 Td (R7) Tj ET")
    path = tmp_path / "overprint.pdf"
    path.write_bytes(_pdf(" ".join(text)))
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        expected = page.dedupe_chars(tolerance=1.0).chars
        kept = pdf_document._first_copies(page.chars)
        assert [id(c) for c in kept] == [id(c) for c in expected]
        assert len(kept) < len(page.chars)


def _grid(lines: int) -> str:
    """A grid of `lines` ruled lines each way, 4pt apart."""
    end = 20 + 4 * (lines - 1)
    rules = [f"20 {20 + 4 * i} m {end} {20 + 4 * i} l S" for i in range(lines)]
    rules += [f"{20 + 4 * i} 20 m {20 + 4 * i} {end} l S" for i in range(lines)]
    return "0.5 w " + " ".join(rules)


def _never(*_: object, **__: object) -> None:
    raise AssertionError("called")


def test_page_of_many_ruled_lines_is_read_as_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 9.4 s before, and about 15 times as long with each doubling: finding
    # tables compares every crossing of two lines with every other.
    parts = "BT /F1 10 Tf 700 700 Td (R1 10k) Tj 0 -14 Td (R2 22k) Tj ET"
    path = tmp_path / "grid.pdf"
    path.write_bytes(_pdf(f"{_grid(160)} {parts}", width=800))
    monkeypatch.setattr(pdfplumber.page.Page, "extract_tables", _never)
    result = pdf_document.parse_pdf(str(path), "G")
    refs = {ref for part in result.inventory.values() for ref in part.refs}
    assert refs == {"R1", "R2"}


def test_ruled_table_within_the_bounds_is_still_found(tmp_path: Path) -> None:
    path = tmp_path / "table.pdf"
    path.write_bytes(_pdf(_grid(40)))
    with pdfplumber.open(path) as pdf:
        tables = pdf_document._tables(pdf.pages[0], 0)
    assert len(tables) == 1
    assert len(tables[0]) == 39


def test_too_many_ruled_lines_to_count_their_crossings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Counting the crossings of 400 lines by 400 would itself take seconds.
    path = tmp_path / "lines.pdf"
    path.write_bytes(_pdf(_grid(400), width=1700, height=1700))
    monkeypatch.setattr(pdfplumber.table, "edges_to_intersections", _never)
    with pdfplumber.open(path) as pdf:
        assert pdf_document._tables(pdf.pages[0], 0) == []


def _drawing(content: bytes, *forms: bytes) -> bytes:
    """A one-page PDF listing 'R1 10k', then drawing the content.

    Args:
        content: More of the page's content stream ('/Fm0 Do').
        forms: Form XObjects, named /Fm0, /Fm1… in the page's resources.
    """
    names = " ".join(f"/Fm{n} {6 + n} 0 R" for n in range(len(forms)))
    return assemble(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> /XObject << "
        + names.encode()
        + b" >> >> >>",
        stream(b"BT /F1 10 Tf 50 700 Td (R1 10k) Tj ET " + content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *forms,
    )


def _form(content: bytes) -> bytes:
    return stream(content, "/Subtype /Form /BBox [0 0 612 792] ")


def _refs(path: Path) -> set[str]:
    result = pdf_document.parse_pdf(str(path), "P")
    return {ref for part in result.inventory.values() for ref in part.refs}


def test_page_that_decompresses_to_gigabytes_is_refused(tmp_path: Path) -> None:
    # pdfminer decompressed every content stream whole before reading it:
    # this 16 KB page took 16 MB, and a 50 MB upload could take 50 GB.
    path = tmp_path / "bomb.pdf"
    path.write_bytes(_drawing(b" " * (16 << 20)))
    assert path.stat().st_size < 20_000
    tracemalloc.start()
    try:
        with pytest.raises(HeavyPageError, match="Page 1 of this PDF draws far more"):
            pdf_document.parse_pdf(str(path), "Bomb")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * pdf_document.MAX_PAGE_CONTENT_BYTES

    result = BOMParserContext().process(
        InputMethod.UPLOAD_FILE, FileUpload("bomb.pdf", path.read_bytes()), "X"
    )
    (error,) = result.stats.errors
    assert error.kind == ErrorKind.UNREADABLE_PDF
    assert error.bug is None


def test_form_the_page_draws_counts_toward_its_drawing(tmp_path: Path) -> None:
    big = _form(b" " * (pdf_document.MAX_PAGE_CONTENT_BYTES + 1))
    path = tmp_path / "form.pdf"
    path.write_bytes(_drawing(b"/Fm0 Do", big))
    with pytest.raises(HeavyPageError):
        _refs(path)

    # A form the page has but never draws isn't read.
    path.write_bytes(_drawing(b"", big))
    assert _refs(path) == {"R1"}


def test_page_drawing_is_counted_as_pdfminer_decodes_it(tmp_path: Path) -> None:
    form = _form(b"BT /F1 10 Tf 50 600 Td (R2 22k) Tj ET")
    path = tmp_path / "page.pdf"
    path.write_bytes(_drawing(b"/Fm0 Do /Fm0 Do", form))
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0].page_obj
        counted = content_bytes(page, pdf_document.MAX_PAGE_CONTENT_BYTES)
        decoded = [s.get_data() for s in page.contents]
        form_stream = pdf.doc.getobj(6)
        assert isinstance(form_stream, PDFStream)
        drawn = form_stream.get_data()
    assert counted == sum(map(len, decoded)) + len(drawn)
    assert content_bytes(page, counted) == counted
    assert content_bytes(page, counted - 1) is None


def test_page_with_a_damaged_stream_is_still_read(tmp_path: Path) -> None:
    # A stream whose checksum is wrong is read up to it, as pdfminer reads it.
    pdf = _drawing(b"", _form(b"BT ET"))
    listing = zlib.compress(b"BT /F1 10 Tf 50 700 Td (R1 10k) Tj ET ")
    damaged = listing[:-1] + bytes([listing[-1] ^ 0xFF])
    path = tmp_path / "damaged.pdf"
    path.write_bytes(pdf.replace(listing, damaged))
    assert path.read_bytes() != pdf
    assert _refs(path) == {"R1"}


def test_page_of_too_many_characters_is_refused(tmp_path: Path) -> None:
    # Reading time grows with a page's characters: 60,000 took 3.2 s.
    path = tmp_path / "crowded.pdf"
    path.write_bytes(_pdf(_tiny_ones(pdf_document.MAX_PAGE_CHARS + 1)))
    with pytest.raises(CrowdedPageError, match="Page 1 of this PDF holds 20,001 "):
        pdf_document.parse_pdf(str(path), "Crowded")

    result = BOMParserContext().process(
        InputMethod.UPLOAD_FILE, FileUpload("crowded.pdf", path.read_bytes()), "X"
    )
    (error,) = result.stats.errors
    assert error.kind == ErrorKind.UNREADABLE_PDF
    assert error.bug is None


def _recorded(sizes: list[tuple[int, int]]) -> ocr.Recognizer:
    def recognizer(image: Image.Image, scale: float) -> list[OcrBox]:
        sizes.append(image.size)
        return []

    return recognizer


def test_huge_page_is_rendered_within_the_pixel_bound(tmp_path: Path) -> None:
    # Rendered at 200 dpi before: this page, 3,000 points square, took 69
    # million pixels, and one 200 inches square would take 1.6 billion (4.8 GB).
    path = tmp_path / "huge.pdf"
    path.write_bytes(_pdf("", width=3000, height=3000))
    result = pdf_document.parse_pdf(str(path), "Huge")
    sizes: list[tuple[int, int]] = []
    assert ocr.needs_ocr(result)
    ocr.attempt_ocr(str(path), result, "Huge", _recorded(sizes))
    ((width, height),) = sizes
    assert width * height <= ocr._MAX_RENDER_PIXELS
    assert width > 3900  # Lowered only as far as the bound needs.


def test_ordinary_pages_are_rendered_as_before() -> None:
    assert ocr.render_resolution(612, 792, 200) == 200
    assert ocr.render_resolution(1191, 1684, 200) == 200  # A2.


def test_page_of_no_size_is_not_rendered(tmp_path: Path) -> None:
    # Its resolution divided by zero.
    path = tmp_path / "empty.pdf"
    path.write_bytes(_pdf("", width=0, height=0))
    result = pdf_document.parse_pdf(str(path), "Empty")
    sizes: list[tuple[int, int]] = []
    assert ocr.needs_ocr(result)
    attempt = ocr.attempt_ocr(str(path), result, "Empty", _recorded(sizes))
    assert attempt is not None
    assert attempt.pages == ()
    assert sizes == []


def test_huge_board_is_rendered_within_the_pixel_bound() -> None:
    image = io.BytesIO()
    Image.new("RGB", (400, 400), "white").save(image, format="PNG")
    png = ", ".join(str(b) for b in image.getvalue())
    content = typeset(
        "#set page(width: 3000pt, height: 3000pt, margin: 0pt)\n"
        f"#place(image(bytes(({png},)), width: 3000pt, height: 3000pt))"
    )
    sizes: list[tuple[int, int]] = []
    board_marks(content, ["R1"], (), _recorded(sizes))
    assert sizes
    assert all(width * height <= ocr._MAX_RENDER_PIXELS for width, height in sizes)
