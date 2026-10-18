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
    _several_values,
    _unlisted,
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

    def test_close_rows_stay_apart(self) -> None:
        # Five Cats: 7pt rows, and a tall box (a wrapped cell) beside them
        # must not chain them into one line.
        boxes = [
            OcrBox("R26", 20, 32, 444, 451),
            OcrBox("20K", 44, 56, 444, 451),
            OcrBox("Ceramic Cap", 202, 230, 440, 458),
            OcrBox("R27", 20, 32, 452, 459),
            OcrBox("20K", 44, 56, 452, 459),
        ]
        words = page_words(boxes)["words"]
        assert len({w["top"] for w in words if w["text"] in {"R26", "R27"}}) == 2

    def test_a_designator_starts_a_cell(self) -> None:
        # SWAH: '16V' and 'D1-D2' read 5pt apart, or as one box.
        for boxes in (
            [OcrBox("16V", 399, 424, 665, 674), OcrBox("D1-D2", 429, 465, 665, 674)],
            [OcrBox("16V D1-D2", 399, 440, 665, 674)],
        ):
            words = [
                OcrBox("C9", 320, 340, 665, 674),
                OcrBox("1µ", 365, 383, 665, 674),
                *boxes,
                OcrBox("1N4148", 502, 542, 665, 674),
            ]
            from src.bom_lib.layout import find_listings

            listings = find_listings(page_words(words)["words"])
            assert [(lst.ref, lst.value) for lst in listings] == [
                ("C9", "1µ 16V"),
                ("D1-D2", "1N4148"),
            ]

    def test_a_description_starts_a_cell(self) -> None:
        # Five Cats: 'TL072 Dual Op Amp' read as one box, and '1N914' 3pt
        # before 'Dual Op Amp'.
        from src.bom_lib.layout import group_lines, split_cells

        for boxes in (
            # Narrow glyphs: a word space is within a cell gap.
            [OcrBox("TL072 Dual Op Amp", 365, 414.3, 665, 674)],
            [
                OcrBox("TL072", 365, 379.5, 665, 674),
                OcrBox("Dual Op Amp", 382, 414, 665, 674),
            ],
        ):
            words = page_words([OcrBox("U1", 320, 334, 665, 674), *boxes])["words"]
            (line,) = group_lines(words)
            assert [c.text for c in split_cells(line)] == ["U1", "TL072", "Dual Op Amp"]

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

    def test_a_table_beside_it_can_start_higher(self) -> None:
        # Samurai Boost: 'Trim1 | 10K | 6mm Trim Pot' rows above 'R1 | 1M'
        # continue a table beside the main one, and 'Boost | Toggle | DPDT'
        # between them reads as no listing.
        def row(top: float, *cells: tuple[str, float]) -> list[OcrBox]:
            return [OcrBox(t, x, x + 6 * len(t), top, top + 9) for t, x in cells]

        boxes = [
            *row(330, ("Trim1", 318), ("10K", 379), ("6mm Trim Pot", 452)),
            *row(341, ("Trim2", 318), ("10K", 379), ("6mm Trim Pot", 452)),
            *row(352, ("Boost", 319), ("Toggle", 380), ("DPDT", 452)),
            *row(
                374, ("R1", 38), ("1M", 100), ("VOL", 318), ("B250K", 380), ("Pot", 453)
            ),
            *[
                b
                for k in range(2, 7)
                for b in row(374 + 11 * (k - 1), (f"R{k}", 38), ("1M", 100))
            ],
            # The board drawing above.
            *row(300, ("Q1", 330), ("C4", 400)),
        ]
        kept = {w["text"] for w in table_block(page_words(boxes))["words"]}
        assert {"Trim1", "Trim2", "Boost", "R1", "R6"} <= kept
        assert "Q1" not in kept

    def test_build_columns_become_a_table(self) -> None:
        # OKS Fuzz: 'Component | ColorSound | 1996 | Dark Meathead' two rows
        # above a value per build; header cells are centred over values.
        def row(top: float, *cells: tuple[str, float]) -> list[OcrBox]:
            return [OcrBox(t, x, x + 6 * len(t), top, top + 9) for t, x in cells]

        boxes = [
            *row(
                464,
                ("Component", 87),
                ("ColorSound", 159),
                ("1996", 236),
                ("Dark", 288),
                ("Meathead", 316),
            ),
            *[
                b
                for k, (ref, a, b_, c) in enumerate(
                    [
                        ("R1", "2M", "1M", "2M"),
                        ("R2", "10k", "18k", "10k"),
                        ("C1", "100n", "47n", "470n"),
                        ("C2", "220p", "470p", "150p"),
                        ("Pot", "A500K", "B500K", "A500K"),
                    ]
                )
                for b in row(488 + 12 * k, (ref, 92), (a, 171), (b_, 237), (c, 304))
            ],
        ]
        (table,) = table_block(page_words(boxes))["tables"]
        assert table[0] == ["Component", "ColorSound", "1996", "Dark Meathead"]
        assert table[2] == ["R2", "10k", "18k", "10k"]

    def test_a_value_and_type_table_is_no_grid(self) -> None:
        def row(top: float, *cells: tuple[str, float]) -> list[OcrBox]:
            return [OcrBox(t, x, x + 6 * len(t), top, top + 9) for t, x in cells]

        boxes = row(464, ("Location", 40), ("Value", 100), ("Type", 170)) + [
            b
            for k in range(5)
            for b in row(
                488 + 12 * k, (f"R{k + 1}", 40), ("10k", 100), ("Resistor", 170)
            )
        ]
        assert table_block(page_words(boxes))["tables"] == []

    def test_a_page_without_a_grid_keeps_nothing(self) -> None:
        boxes = [OcrBox("R9", 50, 66, 150, 164), OcrBox("05", 80, 96, 150, 164)]
        assert table_block(page_words(boxes))["words"] == []


class TestUnlisted:
    """A table row read as no part is named."""

    def test_a_row_with_an_unreadable_value(self) -> None:
        boxes = [
            *_grid_boxes(),
            OcrBox("CLIP1", 180, 220, 500, 514),
            OcrBox("SW_SPDT", 250, 300, 500, 514),
        ]
        block = table_block(page_words(boxes))
        listed = {"R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "IC1", "D1", "D2"}
        assert _unlisted([block], listed) == {"CLIP1"}


class TestPlausible:
    """Values OCR typically misreads are suspect."""

    def test_prose_beside_the_table(self) -> None:
        key = make_component_key(
            ComponentCategory.TRANSISTORS, "J201/2N5457 super small size."
        )
        assert not _plausible(key)

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

    def test_each_hundred_is_a_series(self) -> None:
        # Five Cats numbers the power section 'C100', 'C101' after 'C1'-'C13';
        # the enclosure badge's 'C60' is no capacitor.
        c = (ComponentCategory.CAPACITORS, "10u")
        listed = self._listed(
            **{f"C{n}": c for n in (*range(1, 12), 13, 100, 101)},
        )
        assert _unread(listed, {"C60"}) == {"C12"}

    def test_named_parts_cover_their_kind(self) -> None:
        # DVF names its pots ('GAIN'); its render's 'P1' is one of them.
        listed = self._listed(GAIN=(ComponentCategory.POTENTIOMETERS, "B1M"))
        assert _unread(listed, {"P1"}) == set()


@pytest.mark.parametrize(
    ("value", "several"),
    [("68k 43k", True), ("470n 220nF 47nF", True), ("22n 63V", False), ("10k", False)],
)
def test_several_values(value: str, several: bool) -> None:
    assert _several_values(value) is several


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

        attempt = attempt_ocr(path, result, "Image", lambda *_: _grid_boxes())

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

    def test_several_builds_are_offered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Octavia: a second table relists parts for another build.
        def builds(*_: object, **__: object) -> tuple[Any, ...]:
            variants = []
            for name, value in (("Octavia", "10k"), ("Vintage", "1k")):
                inventory = create_empty_inventory()
                for n in range(1, 7):
                    key = make_component_key(ComponentCategory.RESISTORS, value)
                    inventory.add_part("x", key, f"R{n}", 1)
                variants.append(BuildVariant(name, inventory, 6))
            return create_empty_inventory(), tuple(variants), ()

        monkeypatch.setattr("src.bom_lib.ocr.read_pages", builds)
        path = _blank_pdf(tmp_path / "image.pdf")
        result = parse_pedalpcb_pdf(path, "Image")
        attempt = attempt_ocr(path, result, "Image", lambda *_: _grid_boxes())

        assert attempt is not None
        # Partial: the page also names C1-C3, D1 and IC1, which no build lists.
        assert attempt.verdict == OcrVerdict.PARTIAL
        assert [v.name for v in attempt.variants] == ["Octavia", "Vintage"]
        assert not attempt.inventory
        assert "in 2 builds" in attempt.message

        accepted = ParseResult(result.inventory, result.stats, ocr=attempt).accept_ocr()
        assert accepted.requires_choice
        vintage = accepted.select("Vintage")
        one_k = make_component_key(ComponentCategory.RESISTORS, "1k")
        assert vintage.inventory[one_k]["refs"] == [f"R{n}" for n in range(1, 7)]
        kinds = {w.kind for w in vintage.stats["warnings"]}
        assert ParseWarningKind.NO_PARTS_LIST not in kinds
        assert ParseWarningKind.OCR_READ in kinds

    def test_values_side_by_side_are_partial(self, tmp_path: Path) -> None:
        # Victory III: 'R4 | 22K | 10K' under build columns, read as one list.
        path = _blank_pdf(tmp_path / "image.pdf")
        result = parse_pedalpcb_pdf(path, "Image")
        boxes = [
            OcrBox(
                text,
                40 + 70 * col,
                40 + 70 * col + 8 * len(text),
                400 + 20 * row,
                414 + 20 * row,
            )
            for row, cells in enumerate(
                [
                    ("R1", "10k"),
                    ("R2", "22k 10k"),
                    ("R3", "4k7"),
                    ("R4", "68k 43k"),
                    ("R5", "1M"),
                    ("R6", "2k2"),
                ]
            )
            for col, text in enumerate(cells)
        ]
        attempt = attempt_ocr(path, result, "Image", lambda *_: boxes)

        assert attempt is not None
        assert attempt.verdict == OcrVerdict.PARTIAL
        assert any("side by side" in w.message for w in attempt.warnings)

    def test_unreadable_image(self, tmp_path: Path) -> None:
        path = _blank_pdf(tmp_path / "image.pdf")
        result = parse_pedalpcb_pdf(path, "Image")

        attempt = attempt_ocr(path, result, "Image", lambda *_: [])

        assert attempt is not None
        assert attempt.verdict == OcrVerdict.UNUSABLE
        assert "even with OCR" in attempt.message

    def test_clean_parse_is_not_read(self, tmp_path: Path) -> None:
        def fail(*_: object) -> list[OcrBox]:
            raise AssertionError("OCR must not run")

        result = ParseResult(create_empty_inventory(), _stats())
        assert attempt_ocr(str(tmp_path / "x.pdf"), result, "X", fail) is None

    def test_accept_without_ocr_parts(self) -> None:
        with pytest.raises(ValueError, match="No OCR parts"):
            ParseResult(create_empty_inventory(), _stats()).accept_ocr()
