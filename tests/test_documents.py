"""The build book's and label sheet's contents, and their PDFs."""

import datetime
import io
import zipfile
from dataclasses import replace

import pdfplumber
import pytest

from star_ground.api import Project, generate_master_list
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.manager import rename_source
from star_ground.bom_lib.types import (
    BuildHardware,
    BuiltProject,
    ComponentKey,
    Inventory,
)
from star_ground.bom_lib.units import farads, ohms
from star_ground.exports.documents import (
    ReadablePart,
    build_book,
    capacitor_code,
    condense_refs,
    label_sheet,
    project_checklist,
    readable_part,
    resistor_bands,
)
from star_ground.exports.pack import PackContents, build_pack
from star_ground.exports.typeset import build_book_pdf, label_sheet_pdf

FUZZ = "\n".join(
    [
        "R1 10k",
        "R2 10k",
        "R3 470R",
        "C1 100n",
        "C2 4.7u",
        "D1 1N4148",
        "Q1 2N3904",
        "IC1 TL072",
        "VR1 B100K",
    ]
)
MADE = datetime.date(2026, 11, 7)


@pytest.mark.parametrize(
    ("ohms_value", "expected"),
    [
        ("10000", ("brown", "black", "black", "red", "brown")),
        ("4700", ("yellow", "violet", "black", "brown", "brown")),
        ("470", ("yellow", "violet", "black", "black", "brown")),
        ("1000000", ("brown", "black", "black", "yellow", "brown")),
        ("2.2", ("red", "red", "black", "silver", "brown")),
        ("12345", ()),
    ],
)
def test_a_resistor_s_five_bands(ohms_value: str, expected: tuple[str, ...]) -> None:
    assert resistor_bands(ohms(ohms_value)) == expected


@pytest.mark.parametrize(
    ("farads_value", "code"),
    [("100e-9", "104"), ("2.2e-9", "222"), ("100e-12", "101"), ("47e-12", "47")],
)
def test_a_capacitor_s_printed_code(farads_value: str, code: str) -> None:
    assert capacitor_code(farads(farads_value)) == code


def test_values_are_written_for_a_builder() -> None:
    assert readable_part(
        ComponentCategory.RESISTORS, "4k7", ohms("4700")
    ) == ReadablePart("4.7 kΩ", bands=("yellow", "violet", "black", "brown", "brown"))
    assert readable_part(
        ComponentCategory.CAPACITORS, "100u", farads("100e-6")
    ) == ReadablePart("100 µF", "electrolytic")
    assert readable_part(
        ComponentCategory.CAPACITORS, "100n", farads("100e-9")
    ) == ReadablePart("100 nF", "film", code="104")
    assert readable_part(
        ComponentCategory.POTENTIOMETERS, "B100K DUAL", None
    ) == ReadablePart("100 kΩ", "linear taper, dual")
    assert readable_part(
        ComponentCategory.HARDWARE_MISC, "8 PIN DIP SOCKET", None
    ) == ReadablePart("8-pin DIP socket")
    assert readable_part(ComponentCategory.ICS, "TL072", None) == ReadablePart("TL072")


def test_designators_collapse_into_runs() -> None:
    assert condense_refs(["R1", "R2", "R3", "C1", "Q3", "Q4"]) == "C1, Q3, Q4, R1-R3"
    assert condense_refs(["R1", "R3", "R5"]) == "R1, R3, R5"
    assert condense_refs(["TIME", "R2", "R1"]) == "R1, R2, TIME"
    assert condense_refs([]) == ""


def test_a_checklist_follows_the_build_order() -> None:
    master = generate_master_list([Project(name="Fuzz", data=FUZZ)])
    (project,) = master.projects

    checklist = project_checklist(master.kit, project)

    assert [s.name for s in checklist.stages] == [
        "Resistors",
        "Diodes",
        "Sockets",
        "Small capacitors",
        "Transistors",
        "Electrolytic capacitors",
        "Potentiometers",
        "Chips",
    ]
    resistors = checklist.stages[0].rows
    # Smallest value first, each with its designators.
    assert [(r.part.text, r.refs) for r in resistors] == [
        ("470 Ω", "R3"),
        ("10 kΩ", "R1, R2"),
    ]
    sockets = checklist.stages[2].rows
    assert sockets[0].refs == "IC1"
    assert checklist.stages[-1].rows[0].orientation == "Pin 1 to the notch"
    assert checklist.parts == 10  # nine parts and the socket Star Ground adds


def test_parts_counted_by_value_reach_their_project_s_checklist() -> None:
    """Parts counted by value ('4 - 10k', Tonepad) have no designator, but one
    pedal still needs them."""
    inventory = Inventory()
    key = ComponentKey(ComponentCategory.RESISTORS, "10k")
    inventory.add_counted("Rat", key, 4)
    inventory.add_part("Rat", key, "R9")
    inventory.add_counted("Muff", key, 2)
    inventory = rename_source(inventory, "Rat", "La Rata")
    totals = Inventory()
    totals.merge(inventory, multiplier=2)
    project = BuiltProject("La Rata", "La Rata", BuildHardware(2))

    (stage,) = project_checklist(totals, project).stages

    (row,) = stage.rows
    assert (row.qty, row.refs) == (5, "R9")


def test_the_book_totals_what_to_buy() -> None:
    master = generate_master_list([Project(name="Fuzz", count=2, data=FUZZ)])
    lines = master.shopping_list()

    book = build_book(master.kit, master.projects, lines, MADE, "2.7.0")

    assert book.generated == "7 November 2026"
    assert book.projects[0].count == 2
    assert book.pieces_to_buy == sum(line.buy_qty for line in lines)
    resistors = next(g for g in book.shopping if g.category == "Resistors")
    assert [row.part.text for row in resistors.rows][:1] == ["470 Ω"]


def test_labels_follow_each_project_s_build_order() -> None:
    master = generate_master_list(
        [Project(name="Fuzz", data=FUZZ), Project(name="Boost", data="R1 1M")]
    )

    labels = label_sheet(master.kit, master.projects).labels

    assert labels[0].project == "Fuzz"
    assert labels[0].part.text == "470 Ω"
    assert labels[-1].project == "Boost"
    assert labels[-1].part.text == "1 MΩ"


def _pages(pdf: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return len(document.pages)


def _first_page_text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return document.pages[0].extract_text() or ""


def test_the_book_and_labels_typeset() -> None:
    master = generate_master_list([Project(name="Fuzz", data=FUZZ)])
    book = build_book(master.kit, master.projects, master.shopping_list(), MADE, "x")
    labels = label_sheet(master.kit, master.projects)

    book_pdf = build_book_pdf(book)
    labels_pdf = label_sheet_pdf(labels)

    # A cover, the shopping list, and the one checklist.
    assert _pages(book_pdf) >= 3
    assert "Build book" in _first_page_text(book_pdf)
    assert _pages(labels_pdf) == 1  # ten labels fit on one sheet


def test_thirty_labels_fill_a_sheet() -> None:
    parts = "\n".join(f"R{i} {i}k" for i in range(1, 32))
    master = generate_master_list([Project(name="Many", data=parts)])

    pdf = label_sheet_pdf(label_sheet(master.kit, master.projects))

    assert _pages(pdf) == 2


def test_the_pack_holds_everything_in_one_folder() -> None:
    master = generate_master_list([Project(name="Fuzz", data=FUZZ)])
    pack = build_pack(
        PackContents(
            master.kit,
            master.projects,
            master.shopping_list(),
            stock=master.stock,
            excel_links=True,
            made=MADE,
            version="x",
        )
    )

    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        names = zf.namelist()
        shopping = zf.read("Star Ground build pack 2026-11-07/Shopping list.csv")
    assert {n.split("/")[0] for n in names} == {"Star Ground build pack 2026-11-07"}
    assert len(names) == 5  # no source documents for pasted text
    assert b"=HYPERLINK(" in shopping


def test_source_documents_get_unique_safe_names() -> None:
    master = generate_master_list([Project(name="Fuzz", data=FUZZ)])
    (fuzz,) = master.projects
    labels = ["A/B", "AB", "x\x00y", "???", "../../evil", "Fuzz", "fuzz", " . "]
    projects = [
        replace(fuzz, label=label, document=f"%PDF {i}".encode())
        for i, label in enumerate(labels)
    ]
    pack = build_pack(
        PackContents(
            master.kit,
            projects,
            master.shopping_list(),
            stock=None,
            excel_links=False,
            made=MADE,
            version="x",
        )
    )

    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        sources = [
            (info.filename.split("/Source documents/")[1], zf.read(info))
            for info in zf.infolist()
            if "/Source documents/" in info.filename
        ]
    names = [name for name, _ in sources]
    assert names == [
        "AB.pdf",
        "AB (2).pdf",
        "xy.pdf",
        "Source document.pdf",
        "evil.pdf",
        "Fuzz.pdf",
        "fuzz (2).pdf",
        "Source document (2).pdf",
    ]
    assert [document for _, document in sources] == [
        f"%PDF {i}".encode() for i in range(len(labels))
    ]
    assert all(name.isprintable() for name in names)


def test_a_long_label_makes_a_file_name_any_filesystem_takes() -> None:
    master = generate_master_list([Project(name="Fuzz", data=FUZZ)])
    (fuzz,) = master.projects
    project = replace(fuzz, label="é" * 300, document=b"%PDF")
    pack = build_pack(
        PackContents(
            master.kit,
            [project],
            master.shopping_list(),
            stock=None,
            excel_links=False,
            made=MADE,
            version="x",
        )
    )

    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        (name,) = [n for n in zf.namelist() if "/Source documents/" in n]
    file_name = name.rsplit("/", 1)[1]
    assert file_name.endswith(".pdf")
    assert len(file_name.encode()) < 255


def test_a_regulator_turns_like_a_transistor_not_a_chip() -> None:
    master = generate_master_list([Project(name="Power", data="IC1 78L05\nIC2 TL072")])

    (chips,) = [
        s
        for s in project_checklist(master.kit, master.projects[0]).stages
        if s.name == "Chips"
    ]

    assert {r.part.text: r.orientation for r in chips.rows} == {
        "78L05": "Flat face to the outline",
        "TL072": "Pin 1 to the notch",
    }
