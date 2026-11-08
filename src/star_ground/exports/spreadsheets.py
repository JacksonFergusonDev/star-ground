"""The master list's CSV files: the shopping list, a supplier cart, and stock.

Column headers live here and nowhere else: the shopping list's lines are
typed `ShoppingLine`s, named for a spreadsheet only as they're written.
"""

import csv
import io
import re
from collections.abc import Sequence

from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.types import FORMULA_PREFIXES, ShoppingLine

SHOPPING_LIST_COLUMNS = (
    "Category",
    "Part",
    "Needed",
    "Included with board",
    "In stock",
    "Still needed",
    "Buy",
    "Notes",
    "Search term",
    "Tayda link",
    "Group",
)

# Columns of the supplier cart file. Mouser's and Digi-Key's BOM upload pages
# ask which column holds what, so the names only need to be self-explanatory.
SUPPLIER_CART_COLUMNS = ("Quantity", "Manufacturer Part Number", "Description")

# The stock file's columns, which `read_stock` reads back next time.
STOCK_COLUMNS = ("Category", "Part", "Qty")

# Only these categories name parts by part number ('TL072', '2N5088',
# '42TM018'); a resistor's or capacitor's value never is one.
_PART_NUMBER_CATEGORIES = frozenset(
    {
        ComponentCategory.ICS,
        ComponentCategory.TRANSISTORS,
        ComponentCategory.DIODES,
        ComponentCategory.OPTOELECTRONICS,
        ComponentCategory.TRANSFORMERS,
        ComponentCategory.RELAYS,
    }
)
# One word with letters and digits: '1N4148', 'TQ2-L-5V', 'VTL5C1/2'.
_PART_NUMBER = re.compile(r"(?=.*[A-Z])(?=.*\d)[A-Z0-9][A-Z0-9./-]{2,}")


def _cell(value: object) -> object:
    """A value safe to open in a spreadsheet: text never starts a formula.

    Part values come from documents anyone can write; '=HYPERLINK(...)' read
    as a part must stay text when the CSV opens in Excel.

    Args:
        value: A cell's value.

    Returns:
        Text starting with a formula character prefixed with an apostrophe,
        which spreadsheets read as 'this is text'; anything else unchanged.
    """
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _write(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def shopping_list_csv(
    lines: Sequence[ShoppingLine], excel_links: bool = False
) -> bytes:
    """The shopping list as a CSV, one row per part.

    Args:
        lines: The shopping list.
        excel_links: Whether to write each supplier link as an Excel
            `HYPERLINK` formula, which Excel and Google Sheets show as a
            clickable 'Buy'; a plain URL otherwise.

    Returns:
        The CSV, UTF-8 with a byte order mark so Excel reads 'µ' and 'Ω'.
    """
    rows = []
    for line in lines:
        link: object = _cell(line.supplier_url)
        if excel_links and line.supplier_url:
            # The one formula written on purpose: the app builds the link.
            link = f'=HYPERLINK("{line.supplier_url}", "Buy")'
        rows.append(
            [
                line.category.value,
                _cell(line.part),
                line.bom_qty,
                line.included,
                line.in_stock,
                line.net_need,
                line.buy_qty,
                _cell(line.notes),
                _cell(line.search_term),
                link,
                line.origin.value,
            ]
        )
    return _write(SHOPPING_LIST_COLUMNS, rows).encode("utf-8-sig")


def stock_after_csv(lines: Sequence[ShoppingLine]) -> bytes:
    """The builder's stock after this order arrives and these pedals are built.

    New stock is what was in stock, plus what's bought, less what the builds
    use. Parts that come with a board are used without coming from stock.
    Parts with none left are left out. Star Ground reads this file back as
    stock next time.

    Args:
        lines: The shopping list.

    Returns:
        The CSV, UTF-8 with a byte order mark.
    """
    rows = []
    for line in lines:
        left = line.in_stock + line.buy_qty - (line.bom_qty - line.included)
        if left > 0:
            rows.append([line.category.value, _cell(line.part), left])
    return _write(STOCK_COLUMNS, rows).encode("utf-8-sig")


def manufacturer_part_number(category: ComponentCategory, part: str) -> str:
    """The part number a supplier's BOM tool can match, or '' for a generic part.

    Args:
        category: The part's category.
        part: The part's value as listed ('TL072', '10k', '3mm Red LED').

    Returns:
        The value itself when it is a part number, else an empty string.
    """
    if category in _PART_NUMBER_CATEGORIES and _PART_NUMBER.fullmatch(part.upper()):
        return part
    return ""


def supplier_cart_csv(lines: Sequence[ShoppingLine]) -> bytes:
    """A cart file for a supplier's BOM upload page (Mouser, Digi-Key).

    One line per part to buy. Parts named by a part number carry it, so the
    supplier matches them directly; generic parts (resistors, capacitors,
    pots, hardware) carry a description to choose a match from.

    Args:
        lines: The shopping list.

    Returns:
        The CSV, UTF-8 with no byte order mark: upload pages would read it as
        part of the first header.
    """
    rows = [
        [
            line.buy_qty,
            _cell(manufacturer_part_number(line.category, line.part)),
            _cell(f"{line.category.value}: {line.search_term or line.part}"),
        ]
        for line in lines
        if line.buy_qty > 0
    ]
    return _write(SUPPLIER_CART_COLUMNS, rows).encode("utf-8")
