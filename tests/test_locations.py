"""Tests for finding where each part was read (`src.bom_lib.locations`)."""

from pathlib import Path

from src.bom_lib import (
    ComponentCategory,
    Inventory,
    Location,
    PDFPageExtraction,
    PDFWord,
    create_empty_inventory,
)
from src.bom_lib.locations import locate
from src.bom_lib.ocr import without_ocr
from src.bom_lib.parser import parse_pedalpcb_pdf
from src.bom_lib.types import make_component_key

SAMPLES = Path(__file__).parent / "samples"

_R = ComponentCategory.RESISTORS


def _page(*lines: tuple[float, list[str]]) -> PDFPageExtraction:
    """A page of words: each line's top, and its words 40pt apart from x=40."""
    words = [
        PDFWord(
            text=text, x0=40 + 40 * k, x1=40 + 40 * k + 6 * len(text), top=top, size=10
        )
        for top, texts in lines
        for k, text in enumerate(texts)
    ]
    return {"tables": [], "text": None, "words": words}


def _parts(*parts: tuple[str, str]) -> Inventory:
    inventory = create_empty_inventory()
    for ref, value in parts:
        inventory.add_part("Fuzz", make_component_key(_R, value), ref)
    return inventory


def test_a_part_is_boxed_with_its_value() -> None:
    page = _page((100, ["R1", "10k", "Metal", "film"]))

    located = locate([page], _parts(("R1", "10k")))

    # From the designator to its value, not the rest of the row.
    assert located == {"R1": Location(page=0, x0=40, top=100, x1=98, bottom=110)}


def test_the_parts_list_wins_over_the_silkscreen() -> None:
    board = _page((50, ["R1"]), (80, ["R2"]))
    parts_list = _page((100, ["R1", "10k"]), (120, ["R2", "1M"]))

    located = locate([board, parts_list], _parts(("R1", "10k"), ("R2", "1M")))

    assert {ref: at.page for ref, at in located.items()} == {"R1": 1, "R2": 1}


def test_a_value_printed_another_way_matches() -> None:
    # The board prints R1 with no value; the parts list prints '2K2' for 2.2k.
    page = _page((50, ["R1", "R2"]), (100, ["R1", "2K2"]), (120, ["R2", "4u7"]))

    located = locate([page], _parts(("R1", "2.2k"), ("R2", "4.7u")))

    assert located["R1"].top == 100
    assert located["R2"].top == 120


def test_a_row_ends_at_the_next_column() -> None:
    # Two columns of parts on one line: R1's box stops before C1.
    page = _page((100, ["R1", "10k", "C1", "10k"]))
    inventory = _parts(("R1", "10k"))
    inventory.add_part(
        "Fuzz", make_component_key(ComponentCategory.CAPACITORS, "10k"), "C1"
    )

    located = locate([page], inventory)

    assert located["R1"].x1 < located["C1"].x0


def test_a_value_that_looks_like_a_designator_is_still_the_value() -> None:
    page = _page((100, ["Q4", "J201"]))

    located = locate([page], _parts(("Q4", "J201")))

    assert located["Q4"].x1 == 80 + 6 * 4


def test_a_range_locates_each_of_its_parts() -> None:
    page = _page((100, ["R1-R3", "10k"]))

    located = locate([page], _parts(("R1", "10k"), ("R2", "10k"), ("R3", "10k")))

    assert set(located) == {"R1", "R2", "R3"}
    assert len(set(located.values())) == 1


def test_a_list_of_designators_reaches_its_value() -> None:
    page = _page((100, ["D1,", "D2,", "D3", "1N914"]))

    located = locate([page], _parts(("D1", "1N914"), ("D2", "1N914"), ("D3", "1N914")))

    assert located["D1"].x1 == located["D3"].x1 == 160 + 6 * 5


def test_a_second_boards_designator_takes_the_next_row() -> None:
    page = _page((100, ["R1", "10k"]), (200, ["R1", "1M"]))

    located = locate([page], _parts(("R1", "10k"), ("R1 (2)", "1M")))

    assert located["R1"].top == 100
    assert located["R1 (2)"].top == 200


def test_an_unprinted_part_has_no_location() -> None:
    page = _page((100, ["R1", "10k"]))

    located = locate([page], _parts(("R1", "10k"), ("R9", "10k"), ("IC1 (Inj)", "8")))

    assert set(located) == {"R1"}


def test_pages_are_numbered_as_in_the_document() -> None:
    # OCR reads only some pages: here the document's page 3.
    page = _page((100, ["R1", "10k"]))

    located = locate([page], _parts(("R1", "10k")), page_numbers=[3])

    assert located["R1"].page == 3


def test_every_part_of_a_document_is_located() -> None:
    with without_ocr():
        result = parse_pedalpcb_pdf(str(SAMPLES / "clean" / "Kliche-PedalPCB.pdf"), "K")

    refs = {ref for data in result.inventory.values() for ref in data["refs"]}
    printed = {ref for ref in refs if not ref.endswith(" (Inj)")}
    assert set(result.locations) == printed


def test_each_build_locates_its_own_rows() -> None:
    # Ungula's builds list D1 in different tables, with different values.
    with without_ocr():
        result = parse_pedalpcb_pdf(str(SAMPLES / "dirty" / "Ungula.pdf"), "U")

    rows = {v.name: v.locations["D1"].top for v in result.variants}
    assert len(set(rows.values())) == len(rows) > 1
    chosen = result.select(result.variants[1].name)
    assert chosen.locations == result.variants[1].locations
