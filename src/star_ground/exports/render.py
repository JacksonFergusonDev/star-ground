"""Renders one of the master list's files, by kind, ready to download."""

from dataclasses import dataclass
from enum import StrEnum

from star_ground.exports.documents import build_book, label_sheet
from star_ground.exports.pack import PackContents, build_pack
from star_ground.exports.spreadsheets import (
    shopping_list_csv,
    stock_after_csv,
    supplier_cart_csv,
)
from star_ground.exports.typeset import build_book_pdf, label_sheet_pdf


class ExportKind(StrEnum):
    """The files a master list exports."""

    SHOPPING_LIST = "shopping-list"
    SUPPLIER_CART = "supplier-cart"
    STOCK_UPDATE = "stock-update"
    BUILD_BOOK = "build-book"
    LABELS = "labels"
    BUILD_PACK = "build-pack"


@dataclass(frozen=True, slots=True)
class ExportFile:
    """A rendered file.

    Attributes:
        content: The file's bytes.
        media_type: Its MIME type.
        filename: The name it downloads as.
    """

    content: bytes
    media_type: str
    filename: str


def render_export(kind: ExportKind, contents: PackContents) -> ExportFile:
    """Renders one of the master list's files.

    Args:
        kind: Which file.
        contents: The master list it is made from, and how to write it.

    Returns:
        The file.
    """
    match kind:
        case ExportKind.SHOPPING_LIST:
            csv = shopping_list_csv(contents.lines, contents.excel_links)
            return ExportFile(csv, "text/csv", "shopping-list.csv")
        case ExportKind.SUPPLIER_CART:
            csv = supplier_cart_csv(contents.lines)
            return ExportFile(csv, "text/csv", "supplier-cart.csv")
        case ExportKind.STOCK_UPDATE:
            csv = stock_after_csv(contents.lines)
            return ExportFile(csv, "text/csv", "stock-after-these-builds.csv")
        case ExportKind.BUILD_BOOK:
            book = build_book(
                contents.kit,
                contents.projects,
                contents.lines,
                contents.made,
                contents.version,
            )
            return ExportFile(build_book_pdf(book), "application/pdf", "build-book.pdf")
        case ExportKind.LABELS:
            sheet = label_sheet(contents.kit, contents.projects)
            return ExportFile(
                label_sheet_pdf(sheet), "application/pdf", "labels-avery-5160.pdf"
            )
        case ExportKind.BUILD_PACK:
            return ExportFile(build_pack(contents), "application/zip", "build-pack.zip")
