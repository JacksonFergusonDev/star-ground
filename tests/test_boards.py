"""Tests for finding where a document draws each part on its board."""

import io

from PIL import Image

from star_ground.bom_lib.boards import base_ref, board_marks, marks_by_ref
from star_ground.bom_lib.ocr import OcrBox
from star_ground.bom_lib.types import Location
from tests.pdfs import typeset


def _pdf(draw_image: bool = False) -> bytes:
    """A page with a parts list at the top and board labels below it."""

    def text(x: int, baseline: int, words: str) -> str:
        # A 10pt line's top sits about its cap height (7pt) above its baseline.
        return f"#place(dx: {x}pt, dy: {baseline - 7}pt)[{words}]"

    lines = [
        "#set page(width: 400pt, height: 400pt, margin: 0pt)",
        '#set text(font: "DM Sans 14pt", size: 10pt)',
        text(20, 30, "R1 10k"),
        text(20, 45, "C1 100n"),
        # The board's labels, apart from each other.
        text(200, 250, "R1"),
        text(300, 300, "C1"),
        # Prose naming a part isn't where it's drawn.
        text(20, 380, "Tune R1 to set the bias of the first stage to taste."),
    ]
    if draw_image:
        image = io.BytesIO()
        Image.new("RGB", (400, 200), "white").save(image, format="PNG")
        png = ", ".join(str(b) for b in image.getvalue())
        lines += [
            "#pagebreak()",
            f"#place(dy: 100pt, image(bytes(({png},)), width: 400pt, height: 200pt))",
        ]
    return typeset("\n".join(lines))


def _listing() -> list[Location]:
    return [Location(0, 20, 20, 60, 32), Location(0, 20, 35, 60, 47)]


def _no_ocr(_image: Image.Image, _scale: float) -> list[OcrBox]:
    return []


def test_labels_in_the_text_layer_are_found_outside_the_parts_list() -> None:
    marks = marks_by_ref(board_marks(_pdf(), ["R1", "C1"], _listing(), _no_ocr))

    assert set(marks) == {"R1", "C1"}
    (r1,) = marks["R1"]
    assert r1.page == 0
    assert 195 < r1.x0 < 205
    assert 235 < r1.top < 250


def test_labels_drawn_in_an_image_are_read_by_ocr_both_ways_up() -> None:
    calls: list[tuple[int, int]] = []

    def recognizer(image: Image.Image, scale: float) -> list[OcrBox]:
        calls.append(image.size)
        upright = image.width > image.height
        # Upright, 'Q1' is read; turned, the short sideways 'D1'.
        return [OcrBox("Q1" if upright else "D1", 10, 30, 20, 30)]

    marks = marks_by_ref(
        board_marks(_pdf(draw_image=True), ["Q1", "D1"], (), recognizer)
    )

    assert len(calls) == 2
    assert {loc.page for locs in marks.values() for loc in locs} == {1}
    # Placed on the page: the image starts 100pt down.
    assert marks["Q1"][0].top >= 100
    assert "D1" in marks


def test_a_second_board_s_designator_names_its_part() -> None:
    assert base_ref("R1 (2)") == "R1"
    marks = marks_by_ref(board_marks(_pdf(), ["R1 (2)"], _listing(), _no_ocr))
    assert set(marks) == {"R1"}
