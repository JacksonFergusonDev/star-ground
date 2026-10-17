"""Unit tests for reading image parts lists in `src/bom_lib/ocr.py`.

The OCR engine is replaced by a fake recognizer that returns fixed boxes, so
these tests exercise everything after recognition without running the model.
"""

from pathlib import Path
from typing import Any

import pytest
from fpdf import FPDF

from src.bom_lib import (
    ComponentCategory,
    OcrVerdict,
    ParseWarningKind,
    create_empty_inventory,
    make_component_key,
)
from src.bom_lib.ocr import (
    OcrBox,
    _plausible,
    _unread,
    _verdict,
    attempt_ocr,
    needs_ocr,
    page_words,
    table_block,
)
from src.bom_lib.parser import parse_pedalpcb_pdf
from src.bom_lib.types import BuildVariant, ParseResult, ParseWarning, StatsDict

# A parts table as RapidOCR reads it: one box per cell, 20pt rows, two
# designator columns.
_GRID = [
    ("R1", "10k", "C1", "22n"),
    ("R2", "1M", "C2", "100n"),
    ("R3", "4k7", "C3", "1μ"),
    ("R4", "100k", "IC1", "TL072"),
    ("R5", "2k2", "D1-D2", "1N4148"),
]


def _grid_boxes(top: float = 400.0) -> list[OcrBox]:
    boxes = []
    for row, cells in enumerate(_GRID):
        y = top + 20 * row
        for col, text in enumerate(cells):
            x = 40 + 70 * col
            boxes.append(OcrBox(text, x, x + 8 * len(text), y, y + 14))
    return boxes


def _stats(*warnings: ParseWarning) -> StatsDict:
    stats: StatsDict = {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": {},
        "errors": [],
        "warnings": list(warnings),
    }
    return stats


def _blank_pdf(path: Path) -> str:
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(path))
    return str(path)


class TestPageWords:
    """Recognized boxes become the positioned words the readers expect."""

    def test_a_row_shares_one_top(self) -> None:
        # '63V' read 2pt higher than its row must not start a line of its own.
        boxes = [
            OcrBox("C5", 300, 318, 586, 601),
            OcrBox("22n", 360, 385, 586, 601),
            OcrBox("63V", 398, 424, 584, 602),
        ]
        words = page_words(boxes)["words"]
        assert {w["top"] for w in words} == {586}

    def test_a_box_of_several_words_is_split(self) -> None:
        words = page_words([OcrBox("CA Bi-colour LED", 100, 260, 10, 24)])["words"]
        assert [w["text"] for w in words] == ["CA", "Bi-colour", "LED"]
        assert words[0]["x0"] == 100
        assert words[-1]["x1"] == pytest.approx(260)

    def test_greek_mu_reads_as_micro(self) -> None:
        words = page_words([OcrBox("1μ", 0, 20, 0, 14)])["words"]
        assert words[0]["text"] == "1µ"

    def test_vertical_labels_are_dropped(self) -> None:
        # A PCB render's rotated 'R15' reads as a tall, narrow box.
        words = page_words([OcrBox("R15", 0, 10, 0, 40)])["words"]
        assert words == []


class TestTableBlock:
    """Only the aligned grid of a page is read."""

    def test_keeps_the_grid_and_drops_scattered_labels(self) -> None:
        boxes = [
            *_grid_boxes(),
            # Labels and junk on a PCB render above the table.
            OcrBox("V4", 300, 316, 100, 114),
            OcrBox("IUNMANN", 330, 400, 100, 114),
            OcrBox("R9", 50, 66, 150, 164),
            OcrBox("05", 80, 96, 150, 164),
        ]
        kept = {w["text"] for w in table_block(page_words(boxes))["words"]}
        assert {"R1", "TL072", "D1-D2"} <= kept
        assert not kept & {"V4", "IUNMANN", "R9", "05"}

    def test_a_page_without_a_grid_keeps_nothing(self) -> None:
        boxes = [OcrBox("R9", 50, 66, 150, 164), OcrBox("05", 80, 96, 150, 164)]
        assert table_block(page_words(boxes))["words"] == []


class TestPlausible:
    """Values OCR typically misreads are suspect."""

    @pytest.mark.parametrize(
        ("category", "value", "expected"),
        [
            (ComponentCategory.RESISTORS, "4k7", True),
            (ComponentCategory.RESISTORS, "100", True),
            (ComponentCategory.CAPACITORS, "2n2 63V", True),
            (ComponentCategory.RESISTORS, "05", False),
            (ComponentCategory.RESISTORS, "7k9", False),  # not an E24 value
            (ComponentCategory.CAPACITORS, "Osé", False),
            (ComponentCategory.ICS, "TL072", True),
            (ComponentCategory.ICS, "O", False),
        ],
    )
    def test_value(
        self, category: ComponentCategory, value: str, expected: bool
    ) -> None:
        assert _plausible(make_component_key(category, value)) is expected


class TestUnread:
    """Designators a reading seems to have missed are named."""

    def _listed(self, **parts: tuple[ComponentCategory, str]) -> dict[str, Any]:
        return {ref: make_component_key(cat, val) for ref, (cat, val) in parts.items()}

    def test_a_gap_in_a_series(self) -> None:
        r = (ComponentCategory.RESISTORS, "10k")
        listed = self._listed(R1=r, R2=r, R4=r, R5=r)
        assert _unread(listed, set()) == {"R3"}

    def test_a_designator_printed_elsewhere(self) -> None:
        r = (ComponentCategory.RESISTORS, "10k")
        listed = self._listed(R1=r, R2=r, R3=r)
        # IC1 on the PCB render; 'v4' is a version and 'S4' a pad.
        assert _unread(listed, {"IC1", "v4", "S4", "R2"}) == {"IC1"}

    def test_values_and_numbers_past_the_series_are_not_designators(self) -> None:
        listed = self._listed(
            R1=(ComponentCategory.RESISTORS, "10k"),
            R2=(ComponentCategory.RESISTORS, "10k"),
            Q1=(ComponentCategory.TRANSISTORS, "J201"),
        )
        assert _unread(listed, {"J201", "R60"}) == set()

    def test_named_parts_cover_their_kind(self) -> None:
        # DVF names its pots ('GAIN'); its render's 'P1' is one of them.
        listed = self._listed(GAIN=(ComponentCategory.POTENTIOMETERS, "B1M"))
        assert _unread(listed, {"P1"}) == set()


class TestVerdict:
    """The verdict follows how complete and clean the reading looks."""

    def test_too_few_parts_is_unusable(self) -> None:
        assert _verdict(2, 0, None, 0.0) == OcrVerdict.UNUSABLE

    def test_clean_reading_looks_complete(self) -> None:
        assert _verdict(40, 2, None, 0.0) == OcrVerdict.COMPLETE

    @pytest.mark.parametrize(
        ("suspect", "coverage", "gaps"),
        [(10, None, 0.0), (0, 0.3, 0.0), (0, None, 0.5)],
    )
    def test_messy_or_incomplete_reading_is_partial(
        self, suspect: int, coverage: float | None, gaps: float
    ) -> None:
        assert _verdict(40, suspect, coverage, gaps) == OcrVerdict.PARTIAL


class TestNeedsOcr:
    """OCR is attempted only when a parse looks incomplete."""

    def test_no_parts_list(self) -> None:
        warning = ParseWarning(ParseWarningKind.NO_PARTS_LIST, "", "none")
        result = ParseResult(create_empty_inventory(), _stats(warning))
        assert needs_ocr(result)

    def test_clean_parse(self) -> None:
        assert not needs_ocr(ParseResult(create_empty_inventory(), _stats()))

    def test_document_with_builds(self) -> None:
        warning = ParseWarning(ParseWarningKind.UNEXTRACTED_REF, "R9", "missing")
        variant = BuildVariant("A", create_empty_inventory(), 0)
        result = ParseResult(
            create_empty_inventory(), _stats(warning), variants=(variant,)
        )
        assert not needs_ocr(result)


class TestAttemptOcr:
    """A document whose parts list is an image, end to end."""

    def test_reads_grades_and_offers_the_parts(self, tmp_path: Path) -> None:
        path = _blank_pdf(tmp_path / "image.pdf")
        result = parse_pedalpcb_pdf(path, "Image")
        assert needs_ocr(result)

        attempt = attempt_ocr(path, result, "Image", lambda _: _grid_boxes())

        assert attempt is not None
        assert attempt.verdict == OcrVerdict.COMPLETE
        assert not attempt.supplements
        assert attempt.parts_read == 11  # D1-D2 is two parts
        assert "printed as an image" in attempt.message
        ocr_refs = {
            w.ref for w in attempt.warnings if w.kind == ParseWarningKind.OCR_READ
        }
        assert {"R1", "C3", "IC1", "D1", "D2"} <= ocr_refs

        accepted = ParseResult(result.inventory, result.stats, ocr=attempt).accept_ocr()
        kinds = {w.kind for w in accepted.stats["warnings"]}
        assert ParseWarningKind.NO_PARTS_LIST not in kinds
        assert accepted.ocr is None
        cap = make_component_key(ComponentCategory.CAPACITORS, "1u")
        assert accepted.inventory[cap]["refs"] == ["C3"]
        assert accepted.stats["parts_found"] >= 11

    def test_unreadable_image(self, tmp_path: Path) -> None:
        path = _blank_pdf(tmp_path / "image.pdf")
        result = parse_pedalpcb_pdf(path, "Image")

        attempt = attempt_ocr(path, result, "Image", lambda _: [])

        assert attempt is not None
        assert attempt.verdict == OcrVerdict.UNUSABLE
        assert "even with OCR" in attempt.message

    def test_clean_parse_is_not_read(self, tmp_path: Path) -> None:
        def fail(_: object) -> list[OcrBox]:
            raise AssertionError("OCR must not run")

        result = ParseResult(create_empty_inventory(), _stats())
        assert attempt_ocr(str(tmp_path / "x.pdf"), result, "X", fail) is None

    def test_accept_without_ocr_parts(self) -> None:
        with pytest.raises(ValueError, match="No OCR parts"):
            ParseResult(create_empty_inventory(), _stats()).accept_ocr()
