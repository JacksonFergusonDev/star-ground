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
        excel_links: Whether the shopping list's links are Excel formulas.
        made: The day it's made.
        version: Star Ground's version.
    """

    kit: Inventory
    projects: Sequence[BuiltProject]
    lines: Sequence[ShoppingLine]
    excel_links: bool
    made: datetime.date
    version: str


def _file_name(name: str) -> str:
    """A name safe on every filesystem: no path separators or reserved marks."""
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def _sources(projects: Sequence[BuiltProject]) -> list[tuple[str, bytes]]:
    """Each project's source document that the builder gave, once each."""
    return [
        (f"{SOURCES}/{_file_name(p.label)}.pdf", p.document)
        for p in unique_projects(projects)
        if p.document
    ]


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
        zf.writestr(f"{folder}/{STOCK_AFTER}", stock_after_csv(contents.lines))
        for name, document in sources:
            zf.writestr(f"{folder}/{name}", document)
    return buf.getvalue()
