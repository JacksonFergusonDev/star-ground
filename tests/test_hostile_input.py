"""Long, hostile input is read in linear time.

Each case once took several seconds or minutes, because a regex retried a
scan from every position (or a check compared every line with every other).
A pasted list may be 200,000 characters and an upload far more, and a reading
has no time limit, so each could hold a server core. The bound is generous
(each input took 3 s or more before), so the tests aren't flaky on a slow
runner.
"""

import gc
import time
import zlib
from collections.abc import Callable
from pathlib import Path

import pdfplumber
import pytest

from star_ground.bom_lib import builds, classifier, locations
from star_ground.bom_lib.builds import PageContext, plan_builds
from star_ground.bom_lib.choices import choose_value
from star_ground.bom_lib.enums import ComponentCategory, InputMethod
from star_ground.bom_lib.pdf import bom_table, page_checks, scan
from star_ground.bom_lib.pdf import document as pdf_document
from star_ground.bom_lib.pdf.cells import CONTROL_VALUE
from star_ground.bom_lib.pdf.geometry import Cell, page_lines
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
    PDFPageExtraction,
    PDFWord,
    ReadingStats,
    make_component_key,
)
from star_ground.bom_lib.verification import check_footnotes

_BOUND_S = 1.0


def _timed[T](work: Callable[[], T]) -> tuple[T, float]:
    """The work's result and how long it took, without the garbage collector.

    A test worker holds every corpus reading it has made, and the collector
    walks them all whenever the work allocates enough: under the full suite
    that took over ten times as long as the work itself.
    """
    gc.disable()
    try:
        start = time.perf_counter()
        result = work()
        return result, time.perf_counter() - start
    finally:
        gc.enable()


def _seconds(work: Callable[[], object]) -> float:
    return _timed(work)[1]


def test_pasted_line_with_a_long_run_of_spaces() -> None:
    # 15.6 s before; quadrupled per doubling of the spaces.
    line = "R1 x" + " " * 40000 + "x"
    assert _seconds(lambda: parse_with_verification([line])) < _BOUND_S


def test_pasted_line_is_still_read() -> None:
    result = parse_with_verification(["R1, 10k ", "C1\t100n"])
    assert {k.value for k in result.inventory} == {"10k", "100n"}
    assert result.stats.residuals == []


def test_pasted_value_of_many_slashes() -> None:
    # 14.2 s before, through the paste reader to `choose_value`.
    text = "R1 " + "a/" * 20000 + " 1"
    context = BOMParserContext()
    assert (
        _seconds(lambda: context.process(InputMethod.PASTE_TEXT, text, "s")) < _BOUND_S
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("a/" * 20000 + " 1", id="slashes"),  # 14 s
        pytest.param("1" * 40000 + "!", id="digits"),  # 30 s
        pytest.param("a" + " " * 40000 + "b", id="spaces"),  # 4 s
        pytest.param("(or a" + " " * 40000 + "b", id="open-alternative"),  # 11 s
        pytest.param("(a" + " " * 40000 + "b", id="open-will-also-work"),
    ],
)
def test_choose_value_on_long_values(value: str) -> None:
    assert _seconds(lambda: choose_value("R1", value)) < _BOUND_S


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
    value = "DIP1 " * 20000
    assert _seconds(lambda: classifier.categorize_part("FOO", value)) < _BOUND_S
    assert classifier.DIP_SWITCH.search(value) is None
    assert classifier.DIP_SWITCH.search(value + "SWITCH") is not None
    assert classifier.DIP_SWITCH.search("X DIP8 8 POSITION SWITCH") is not None
    assert classifier.DIP_SWITCH.search("DIP8\nSWITCH") is None


def test_many_either_notes_on_a_page() -> None:
    # 9.2 s before: each 'either' scanned the rest of the page for 'control'.
    page = PageContext([], "either " * 14000 + "x control or y")
    assert _seconds(lambda: plan_builds([], [page])) < _BOUND_S
    assert builds._EITHER_OR.search("either Nature control, or Treble + Bass controls")


def test_many_starred_lines() -> None:
    # 5.5 s before: every '*' heading matched against every line.
    text = "\n".join(f"*a{i}" for i in range(4000))
    assert _seconds(lambda: check_footnotes([text], ReadingStats())) < _BOUND_S


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
    # 4.7 s per match before, and a cell is matched several times.
    assert _seconds(lambda: CONTROL_VALUE.match("1" * 40000)) < _BOUND_S
    assert CONTROL_VALUE.match('1/4" Switching TRS') is not None


def test_unclosed_aside_after_many_parentheses() -> None:
    # 3.3 s before: each '(' rescanned the rest of the cell for ')'.
    cell = "(" * 40000 + ")"
    assert _seconds(lambda: bom_table._without_unclosed_aside(cell)) < _BOUND_S
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


def test_page_of_many_small_numbers() -> None:
    # 22.7 s before: every number was compared with every word on the page.
    words = [
        _word("1", 10 + (i % 100) * 6, 10 + (i // 100) * 8, 6.0) for i in range(20000)
    ]
    assert _seconds(lambda: footnote_marks(words)) < _BOUND_S


def test_page_of_many_footnote_marks() -> None:
    # 6.2 s before (`marked_labels`), and `footnote_marks` was quadratic too.
    words = _footnoted_values(8000)
    assert _seconds(lambda: marked_labels(words)) < _BOUND_S
    marks = footnote_marks(words)
    assert len(marks) == 8000
    assert all(m["text"] == "1" for m in marks)


def _pdf(content: str) -> bytes:
    """A one-page PDF drawing the content stream in Helvetica."""
    stream = zlib.compress(content.encode("latin-1"))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
        + stream
        + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    return out + f"startxref\n{xref}\n%%EOF\n".encode()


def test_dropping_many_footnote_marks_from_a_page(tmp_path: Path) -> None:
    # 6.5 s before: every character was compared with every mark.
    text = []
    for i in range(5000):
        x, y = 10 + (i % 40) * 15, 780 - (i // 40) * 6
        text.append(f"BT /F1 4 Tf {x} {y} Td (10k) Tj /F1 2 Tf 8 1 Td (1) Tj ET")
    path = tmp_path / "marks.pdf"
    path.write_bytes(_pdf(" ".join(text)))
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        assert page.chars  # Reading the PDF itself isn't what's timed.
        chars, seconds = _timed(lambda: pdf_document._without_marks(page).chars)
        assert seconds < _BOUND_S
    assert "".join(c["text"] for c in chars) == "10k" * 5000


def _valueless_refs_alone(
    words: list[PDFWord], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], float]:
    """`valueless_refs` and its time, with the page's listings found beforehand.

    Finding listings isn't what these tests time, and it is most of the work.
    """
    listings = find_listings(words)
    monkeypatch.setattr(page_checks, "find_listings", lambda _: listings)
    return _timed(lambda: valueless_refs(words))


def test_designator_column_of_a_long_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # 6.5 s before: each cell was compared with every listing and every word.
    words = []
    for i in range(8000):
        x, top = 10 + (i % 2) * 60, 10 + (i // 2) * 12
        words += [_word(f"R{i % 999 + 1}", x, top), _word("10k", x + 25, top)]
    refs, seconds = _valueless_refs_alone(words, monkeypatch)
    assert seconds < _BOUND_S
    assert refs == []


def test_valueless_designators_of_a_long_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 3.6 s before.
    words = []
    for i in range(8000):
        words.append(_word(f"R{i % 900 + 1}", 10, 10 + i * 12))
        if i % 2:
            words.append(_word("10k", 60, 10 + i * 12))
    refs, seconds = _valueless_refs_alone(words, monkeypatch)
    assert seconds < _BOUND_S
    assert len(refs) == 4000
    assert set(refs) == {f"R{n}" for n in range(1, 901, 2)}


def test_long_page_of_typed_listings() -> None:
    # 6.6 s before: each typed cell re-typed every cell on the page.
    words = []
    for i in range(4000):
        x, top = 10 + (i % 10) * 120, 10 + (i // 10) * 12
        words += [
            _word(f"R{i % 999 + 1}", x, top),
            _word("10k", x + 25, top),
            _word("Resistor", x + 55, top),
        ]
    lines = page_lines(words)
    columns, seconds = _timed(lambda: scan._type_columns(lines))
    assert seconds < _BOUND_S
    assert columns == {65 + 120 * k for k in range(10)}
    listings = find_listings(words)
    assert len(listings) == 4000
    assert {lst.value for lst in listings} == {"10k"}


def test_long_part_description() -> None:
    # 82 s before: each step rebuilt the texts of the whole line.
    cells = [Cell((_word("POT", 10 + 20 * i, 10),)) for i in range(20000)]
    assert _seconds(lambda: _description_end(cells, 0, frozenset())) < _BOUND_S
    assert _description_end(cells, 0, frozenset()) == 20000


def test_designator_printed_many_times_on_a_line() -> None:
    # 51 s before: each mention searched the rest of the line for its value.
    inventory = Inventory()
    inventory.add_part(
        "s", make_component_key(ComponentCategory.RESISTORS, "10k"), "R1", 1
    )
    words = [_word("R1,", 10 + 25 * i, 100) for i in range(8000)] + [
        _word("10k", 10 + 25 * 8000, 100)
    ]
    page = PDFPageExtraction(tables=[], text="", words=words)
    located, seconds = _timed(lambda: locations.locate([page], inventory))
    assert seconds < _BOUND_S
    assert located["R1"].x1 == words[-1]["x1"]


def test_number_cache_is_bounded() -> None:
    # Keyed by words from uploaded documents: unbounded, it grows for the
    # server's life.
    assert locations._number.cache_info().maxsize is not None
