"""The build pack: every printed document and spreadsheet in one ZIP.

Everything sits in one folder named for the day, so unzipping it never
scatters files into the folder it's opened in. The build book's cover lists
the rest of the pack, so the pack needs no separate read-me.
"""

import datetime
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass

from star_ground.bom_lib.types import BuiltProject, Inventory, ShoppingLine
from star_ground.exports.documents import (
    PackFile,
    build_book,
    label_sheet,
    unique_projects,
)
from star_ground.exports.spreadsheets import (
    shopping_list_csv,
    stock_after_csv,
    supplier_cart_csv,
)
from star_ground.exports.typeset import build_book_pdf, label_sheet_pdf

BUILD_BOOK = "Build book.pdf"
LABELS = "Labels (Avery 5160).pdf"
SHOPPING_LIST = "Shopping list.csv"
SUPPLIER_CART = "Supplier cart.csv"
STOCK_AFTER = "Stock after these builds.csv"
SOURCES = "Source documents"


@dataclass(frozen=True, slots=True)
class PackContents:
    """What a build pack is made from.

    Attributes:
        kit: The master list's parts, hardware kit included.
        projects: The master list's projects.
        lines: The shopping list.
        stock: The builder's stock, if given.
        excel_links: Whether the shopping list's links are Excel formulas.
        made: The day it's made.
        version: Star Ground's version.
    """

    kit: Inventory
    projects: Sequence[BuiltProject]
    lines: Sequence[ShoppingLine]
    stock: Inventory | None
    excel_links: bool
    made: datetime.date
    version: str


# A file name's longest stem, in characters: under the 255 bytes
# filesystems allow, with room for ' (2)' and '.pdf', even when every
# character takes UTF-8's 4 bytes.
_MAX_STEM = 60
# Characters some filesystem refuses in a name, and control characters,
# which zipfile cuts a name at (NUL) or extractors refuse.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_FALLBACK = "Source document"


def _file_name(name: str) -> str:
    """A file name stem from a label, without separators or marks.

    Dots and spaces are trimmed from the ends, so a name is never hidden
    ('.x') or a path ('..'), and Windows keeps it as written.

    Args:
        name: A project's label.

    Returns:
        The stem, or a fallback when nothing of the label is left.
    """
    stem = _UNSAFE.sub("", name)[:_MAX_STEM].strip(" .")
    return stem or _FALLBACK


def _sources(projects: Sequence[BuiltProject]) -> list[tuple[str, bytes]]:
    """Each project's source document that the builder gave, once each.

    Names are made safe first, then numbered where two are alike ('A/B'
    and 'AB'; 'Fuzz' and 'fuzz', which are one file on macOS and Windows).
    """
    taken: set[str] = set()
    sources = []
    for project in unique_projects(projects):
        if not project.document:
            continue
        stem = _file_name(project.label)
        name, n = stem, 1
        while name.casefold() in taken:
            n += 1
            name = f"{stem} ({n})"
        taken.add(name.casefold())
        sources.append((f"{SOURCES}/{name}.pdf", project.document))
    return sources


def build_pack(contents: PackContents) -> bytes:
    """Writes the build pack.

    Args:
        contents: What it's made from.

    Returns:
        The ZIP's bytes.
    """
    sources = _sources(contents.projects)
    files = [
        PackFile(LABELS, "Labels for each part's bag or drawer, 30 to a sheet."),
        PackFile(SHOPPING_LIST, "Every part to buy, for a spreadsheet."),
        PackFile(SUPPLIER_CART, "For Mouser's or Digi-Key's BOM upload page."),
        PackFile(
            STOCK_AFTER,
            "Your stock once this order arrives and these pedals are built. "
            "Give it to Star Ground as your stock next time.",
        ),
    ]
    if sources:
        files.append(PackFile(f"{SOURCES}/", "The documents you gave, as given."))
    book = build_book(
        contents.kit,
        contents.projects,
        contents.lines,
        contents.made,
        contents.version,
        files,
    )
    folder = f"Star Ground build pack {contents.made.isoformat()}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/{BUILD_BOOK}", build_book_pdf(book))
        zf.writestr(
            f"{folder}/{LABELS}",
            label_sheet_pdf(label_sheet(contents.kit, contents.projects)),
        )
        zf.writestr(
            f"{folder}/{SHOPPING_LIST}",
            shopping_list_csv(contents.lines, contents.excel_links),
        )
        zf.writestr(f"{folder}/{SUPPLIER_CART}", supplier_cart_csv(contents.lines))
        zf.writestr(
            f"{folder}/{STOCK_AFTER}",
            stock_after_csv(contents.lines, contents.kit, contents.stock),
        )
        for name, document in sources:
            zf.writestr(f"{folder}/{name}", document)
    return buf.getvalue()
