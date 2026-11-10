"""Tests for the backend API a frontend calls (`star_ground.api`)."""

import ast
import datetime
import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from star_ground.api import (
    PENDING_SOURCE,
    Correction,
    Project,
    document_key,
    file_title,
    generate_master_list,
    read_correction,
    read_document,
    read_stock,
)
from star_ground.bom_lib import (
    ComponentCategory,
    ComponentKey,
    FileUpload,
    InputMethod,
    Inventory,
    ParseResult,
    ReadingStats,
)
from star_ground.bom_lib.constants import IC_SOCKET
from star_ground.bom_lib.errors import (
    BuildNotChosenError,
    FetchError,
    UnreadablePartError,
)
from star_ground.exports.documents import build_book
from star_ground.exports.pack import PackContents, build_pack

SAMPLES = Path(__file__).parent / "samples"
SRC = Path(__file__).parent.parent / "src" / "star_ground"

R10K = ComponentKey(ComponentCategory.RESISTORS, "10k")
R1K = ComponentKey(ComponentCategory.RESISTORS, "1k")


def _upload(path: Path) -> FileUpload:
    return FileUpload(path.name, path.read_bytes())


def test_pasted_projects_merge_into_one_master_list() -> None:
    master = generate_master_list(
        [
            Project(name="Fuzz", count=2, data="R1 10k\nC1 100n"),
            Project(data="R1 10k"),
        ]
    )

    assert [p.name for p in master.projects] == ["Fuzz", "Project #2"]
    assert master.parts[R10K].sources == {
        "Fuzz": ["R1", "R1"],
        "Project #2": ["R1"],
    }
    assert master.stats.parts_found == 3
    # The hardware kit is added for all three pedals, not to the documents' parts.
    assert len(master.kit) > len(master.parts)
    row = next(r for r in master.shopping_list() if r.part == "10k")
    assert row.bom_qty == 3


def test_a_parts_list_s_chips_get_sockets() -> None:
    master = generate_master_list([Project(data="IC1 TL071\nIC100 TC1044SCPA")])

    socket = ComponentKey(ComponentCategory.HARDWARE_MISC, "DIP SOCKET (Check Size)")
    assert master.parts[socket].qty == 2
    assert any(r.part == "DIP SOCKET (Check Size)" for r in master.shopping_list())


def test_a_multi_circuit_document_has_no_default_build() -> None:
    upload = _upload(SAMPLES / "dirty" / "Ungula.pdf")
    reading = read_document(InputMethod.UPLOAD_FILE, upload)
    assert reading is not None
    assert reading.variant_names == ("Stock", "Cleft Mod")

    project = Project(method=InputMethod.UPLOAD_FILE, data=upload, reading=reading)
    with pytest.raises(BuildNotChosenError) as raised:
        generate_master_list([project])
    assert raised.value.projects == ["Ungula"]

    cleft = generate_master_list([replace(project, variant="Cleft Mod")])
    (built,) = cleft.projects
    assert built.label == "Ungula (Cleft Mod)"
    q1 = ComponentKey(ComponentCategory.TRANSISTORS, "2N5089")
    assert "Q1" in cleft.parts[q1].sources["Ungula (Cleft Mod)"]
    assert built.document == upload.content

    # The reading is reused untouched: another build can still be generated.
    stock = generate_master_list([replace(project, variant="Stock")])
    assert stock.projects[0].label == "Ungula (Stock)"
    assert all(
        set(d.sources) == {PENDING_SOURCE}
        for v in reading.variants
        for d in v.inventory.values()
    )


def test_input_errors_reach_the_master_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str) -> None:
        raise FetchError("The link opened a web page, not a parts list.")

    monkeypatch.setattr("star_ground.bom_lib.strategies.context.fetch_url", refuse)
    master = generate_master_list(
        [
            Project(data="R1 10k"),
            Project(method=InputMethod.FROM_URL, data="https://example.test/a.pdf"),
        ]
    )

    assert [e.message for e in master.stats.errors] == [
        "The link opened a web page, not a parts list."
    ]
    assert master.projects[1].errors == tuple(master.stats.errors)
    assert master.stats.parts_found == 1


def test_stock_is_subtracted_from_what_to_buy() -> None:
    stock = read_stock(FileUpload("stock.csv", b"Category,Part,Qty\nResistors,10k,2\n"))
    master = generate_master_list(
        [Project(name="Fuzz", count=5, data="R1 10k")], stock=stock
    )

    row = next(r for r in master.shopping_list() if r.part == "10k")
    assert (row.bom_qty, row.in_stock, row.net_need) == (5, 2, 3)


def test_documents_are_named_by_label_and_keep_the_source() -> None:
    upload = _upload(SAMPLES / "clean" / "Kliche-PedalPCB.pdf")
    master = generate_master_list(
        [Project(name="Kliche", method=InputMethod.UPLOAD_FILE, data=upload)]
    )

    pack = build_pack(
        PackContents(
            master.kit,
            master.projects,
            master.shopping_list(),
            stock=master.stock,
            excel_links=False,
            made=datetime.date(2026, 11, 7),
            version="test",
        )
    )

    folder = "Star Ground build pack 2026-11-07"
    with zipfile.ZipFile(io.BytesIO(pack)) as zf:
        assert zf.read(f"{folder}/Source documents/Kliche.pdf") == upload.content
        assert {
            f"{folder}/Build book.pdf",
            f"{folder}/Labels (Avery 5160).pdf",
            f"{folder}/Shopping list.csv",
            f"{folder}/Supplier cart.csv",
            f"{folder}/Stock after these builds.csv",
        } <= set(zf.namelist())


def test_an_upload_is_known_by_its_content() -> None:
    a = FileUpload("bom.csv", b"R1,10k")
    assert document_key(InputMethod.UPLOAD_FILE, a) == document_key(
        InputMethod.UPLOAD_FILE, FileUpload("bom.csv", b"R1,10k")
    )
    assert document_key(InputMethod.UPLOAD_FILE, a) != document_key(
        InputMethod.UPLOAD_FILE, FileUpload("bom.csv", b"R1,22k")
    )
    assert document_key(InputMethod.PASTE_TEXT, "R1 10k") is None
    assert read_document(InputMethod.PASTE_TEXT, "R1 10k") is None


@pytest.mark.parametrize(
    ("method", "data", "title"),
    [
        (InputMethod.UPLOAD_FILE, FileUpload("FCP-Tone.pdf", b""), "FCP-Tone"),
        (
            InputMethod.FROM_URL,
            "https://x.test/f/FCP-Tone%20Monarch.pdf",
            "FCP-Tone Monarch",
        ),
        (InputMethod.PASTE_TEXT, "R1 10k", None),
    ],
)
def test_file_title(method: InputMethod, data: object, title: str | None) -> None:
    assert file_title(method, data) == title  # type: ignore[arg-type]


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    "path", list(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC))
)
def test_the_backend_does_not_depend_on_a_frontend(path: Path) -> None:
    """No module outside the server imports the web framework."""
    if path.is_relative_to(SRC / "server"):
        pytest.skip("the server is the frontend's side of the API")
    assert not {m for m in _imports(path) if m.split(".")[0] == "fastapi"}


def test_a_short_stock_row_is_skipped() -> None:
    stock = read_stock(
        FileUpload("stock.csv", b"Category,Part,Qty\nResistors,10k\nResistors,22k,4\n")
    )

    assert {k.value: d.qty for k, d in stock.items()} == {"22k": 4}


@pytest.mark.parametrize("count", [0, -2])
def test_a_project_needs_at_least_one_pedal(count: int) -> None:
    with pytest.raises(ValueError, match="at least one pedal"):
        Project(name="Fuzz", count=count, data="R1 10k")


def test_different_projects_with_one_name_are_kept_apart() -> None:
    master = generate_master_list(
        [
            Project(name="Fuzz", data="R1 10k\nC1 100n"),
            Project(name="Fuzz", data="R1 22k\nQ1 2N3904"),
        ]
    )

    assert [p.label for p in master.projects] == ["Fuzz", "Fuzz (2)"]
    assert master.parts[R10K].sources == {"Fuzz": ["R1"]}
    book = build_book(
        master.kit, master.projects, master.shopping_list(), datetime.date.today(), ""
    )
    assert [p.name for p in book.projects] == ["Fuzz", "Fuzz (2)"]


def test_the_same_project_entered_twice_shares_its_label() -> None:
    master = generate_master_list(
        [Project(name="Fuzz", data="R1 10k"), Project(name="Fuzz", data="R1 10k")]
    )

    assert [p.label for p in master.projects] == ["Fuzz", "Fuzz"]
    assert master.parts[R10K].qty == 2


def test_a_correction_buys_the_part_the_document_prints() -> None:
    project = Project(
        name="Fuzz",
        count=2,
        data="R1 1k\nR2 1k",
        corrections=(Correction("R1", R10K),),
    )

    master = generate_master_list([project])

    one_k = ComponentKey(ComponentCategory.RESISTORS, "1k")
    assert master.parts[R10K].qty == 2
    assert master.parts[one_k].qty == 2
    assert {p.ref: p.value for p in master.projects[0].parts} == {
        "R1": "10k",
        "R2": "1k",
    }


def test_a_correction_leaves_the_reading_it_corrects_unchanged() -> None:
    # The server keeps readings in a cache and builds every list from them.
    upload = FileUpload("fuzz.csv", b"Ref,Value\nR1,1k\nR2,1k\n")
    reading = read_document(InputMethod.UPLOAD_FILE, upload)
    assert reading is not None
    before = repr(reading)
    project = Project(
        name="Fuzz",
        method=InputMethod.UPLOAD_FILE,
        data=upload,
        reading=reading,
        corrections=(Correction("R1", R10K),),
    )

    master = generate_master_list([project])

    assert master.parts[R10K].qty == 1
    assert repr(reading) == before


def test_a_correction_that_leaves_a_part_unused_removes_it() -> None:
    project = Project(data="R1 1k", corrections=(Correction("R1", R10K),))

    master = generate_master_list([project])

    assert ComponentKey(ComponentCategory.RESISTORS, "1k") not in master.parts


def test_a_correction_to_a_part_not_listed_changes_nothing() -> None:
    project = Project(data="R1 1k", corrections=(Correction("R9", R10K),))

    master = generate_master_list([project])

    assert R10K not in master.parts


TL072 = ComponentKey(ComponentCategory.ICS, "TL072")
REGULATOR = ComponentKey(ComponentCategory.ICS, "78L05")


@pytest.mark.parametrize(
    ("read", "correction", "printed"),
    [
        # A chip corrected to a regulator loses its socket...
        ("IC1 TL072\nR1 10k", Correction("IC1", REGULATOR), "IC1 78L05\nR1 10k"),
        # ...and a regulator corrected to a chip gains one.
        ("IC1 78L05\nR1 10k", Correction("IC1", TL072), "IC1 TL072\nR1 10k"),
        ("R1 1k\nR2 1k", Correction("R1", R10K), "R1 10k\nR2 1k"),
    ],
)
def test_a_correction_gives_the_list_of_a_document_printing_it(
    read: str, correction: Correction, printed: str
) -> None:
    corrected = generate_master_list(
        [Project(name="Fuzz", data=read, corrections=(correction,))]
    )
    document = generate_master_list([Project(name="Fuzz", data=printed)])

    assert dict(corrected.kit) == dict(document.kit)


def test_a_correction_adds_no_socket_where_the_document_lists_its_own() -> None:
    # Aion FX lists 'IC1-S | DIP-8 socket', so the reading adds none.
    inventory = Inventory()
    inventory.add_part(PENDING_SOURCE, REGULATOR, "IC1")
    socket = ComponentKey(ComponentCategory.HARDWARE_MISC, "DIP-8 socket")
    inventory.add_part(PENDING_SOURCE, socket, "IC1-S")
    reading = ParseResult(inventory=inventory, stats=ReadingStats())
    project = Project(
        name="Fuzz",
        method=InputMethod.UPLOAD_FILE,
        data=FileUpload("fuzz.pdf", b"%PDF"),
        reading=reading,
        corrections=(Correction("IC1", TL072),),
    )

    master = generate_master_list([project])

    assert master.parts[TL072].qty == 1
    assert IC_SOCKET not in master.parts


def test_a_correction_moves_every_part_its_designator_stands_for() -> None:
    blocks = ComponentKey(ComponentCategory.HARDWARE_MISC, "TERMINAL BLOCK")
    screws = ComponentKey(ComponentCategory.HARDWARE_MISC, "SCREW TERMINAL")
    inventory = Inventory()
    inventory.add_part(PENDING_SOURCE, blocks, "TB1", qty=3)
    inventory.add_part(PENDING_SOURCE, blocks, "TB2")
    reading = ParseResult(inventory=inventory, stats=ReadingStats())
    project = Project(
        name="Fuzz",
        count=2,
        method=InputMethod.UPLOAD_FILE,
        data=FileUpload("fuzz.pdf", b"%PDF"),
        reading=reading,
        corrections=(Correction("TB1", screws),),
    )

    master = generate_master_list([project])

    assert master.parts[screws].qty == 6
    assert master.parts[blocks].qty == 2
    assert master.parts[blocks].refs == ["TB2"]
    # How many its designator stands for follows it to the project's label.
    assert master.parts[screws].multiples == {"Fuzz": {"TB1": 3}}
    assert master.parts[blocks].multiples == {}


def test_the_same_document_corrected_two_ways_is_two_lists() -> None:
    plain = Project(name="Fuzz", data="R1 10k")
    fixed = replace(plain, corrections=(Correction("R1", R1K),))

    master = generate_master_list([plain, fixed])

    assert [p.label for p in master.projects] == ["Fuzz", "Fuzz (2)"]
    assert dict(master.parts[R10K].sources) == {"Fuzz": ["R1"]}
    assert dict(master.parts[R1K].sources) == {"Fuzz (2)": ["R1"]}


def test_the_same_corrections_in_any_order_share_a_label() -> None:
    first = Project(
        name="Fuzz",
        data="R1 10k\nR2 10k",
        corrections=(Correction("R1", R1K), Correction("R2", R1K)),
    )
    second = replace(first, corrections=first.corrections[::-1])

    master = generate_master_list([first, second])

    assert [p.label for p in master.projects] == ["Fuzz", "Fuzz"]


ENCLOSURE = ComponentKey(ComponentCategory.HARDWARE_MISC, "1590B Enclosure")


def test_a_project_with_no_parts_gets_no_hardware() -> None:
    master = generate_master_list(
        [
            Project(name="Fuzz", count=2, data="R1 10k"),
            Project(name="Unread", count=3, data="nothing to read here"),
        ]
    )

    assert master.kit[ENCLOSURE].qty == 2


def test_a_master_list_of_no_projects_has_no_hardware() -> None:
    master = generate_master_list([])

    assert len(master.kit) == 0


@pytest.mark.parametrize(
    ("ref", "value", "category", "part"),
    [
        ("R1", "2K2", None, ("Resistors", "2.2k")),
        ("C1", "100nF", None, ("Capacitors", "100n")),
        (
            "DRIVE",
            "B100k",
            ComponentCategory.POTENTIOMETERS,
            ("Potentiometers", "B100k"),
        ),
    ],
)
def test_a_correction_is_read_as_a_parts_list_row(
    ref: str,
    value: str,
    category: ComponentCategory | None,
    part: tuple[str, str],
) -> None:
    key = read_correction(ref, value, category)

    assert (key.category.value, key.value) == part


def test_a_blank_correction_is_explained() -> None:
    with pytest.raises(UnreadablePartError, match="R1"):
        read_correction("R1", "  ")
