import csv
import io
import re

from src.bom_lib.enums import ComponentCategory
from src.bom_lib.types import ShoppingListRow

BASE_SHOPPING_LIST_COLS = [
    "Category",
    "Part",
    "BOM Qty",
    "Buy Qty",
    "Notes",
    "Tayda_Link",
    "Origin",
]


def get_shopping_list_columns(
    has_stock: bool = False, include_search_term: bool = False
) -> list[str]:
    """Returns the ordered column names for shopping list display and export.

    Args:
        has_stock: If True, includes 'In Stock' and 'Net Need' columns.
        include_search_term: If True, includes the 'Search Term' column.

    Returns:
        List of column name strings in canonical order.
    """
    cols = BASE_SHOPPING_LIST_COLS.copy()
    if include_search_term:
        cols.insert(5, "Search Term")
    if has_stock:
        cols[3:3] = ["In Stock", "Net Need"]
    return cols


def generate_shopping_list_csv(
    data: list[ShoppingListRow], use_excel_formulas: bool = False
) -> bytes:
    """Generates a CSV file for the shopping list.

    Constructs a UTF-8 encoded CSV string (with BOM signature) suitable for
    download. It adapts the columns dynamically based on whether 'Net Need'
    data is available (i.e., if stock was checked).

    Args:
        data (list[ShoppingListRow]): The list of row dictionaries to write.
        use_excel_formulas (bool):  If True, formats URLs as Excel `=HYPERLINK()` formulas.
                                    If False, writes raw URL strings.

    Returns:
        bytes: The CSV content encoded as utf-8-sig.
    """
    csv_buf = io.StringIO()

    # Define columns based on data presence
    fields = get_shopping_list_columns(
        has_stock=bool(data and "Net Need" in data[0]),
        include_search_term=True,
    )

    writer = csv.DictWriter(csv_buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()

    rows_to_write = []
    for row in data:
        if use_excel_formulas and row.get("Tayda_Link"):
            # Transform the link into a clickable formula
            clean_row = row.copy()
            clean_row["Tayda_Link"] = f'=HYPERLINK("{row["Tayda_Link"]}", "Buy")'
            rows_to_write.append(clean_row)
        else:
            rows_to_write.append(row)

    writer.writerows(rows_to_write)

    # encode "utf-8-sig" to ensure Excel opens it correctly with special characters
    return csv_buf.getvalue().encode("utf-8-sig")


def generate_stock_update_csv(data: list[ShoppingListRow]) -> bytes:
    """Calculates updated stock levels and generates a CSV import file.

    Logic:
        New Stock = (Current Stock + Buy Qty) - Used Qty

    Parts that come with a PCB are used without coming from stock.

    This file is intended to be re-uploaded by the user next time they use
    the app, closing the logistics loop.

    Args:
        data (list[ShoppingListRow]): The processed master list data.

    Returns:
        bytes: The CSV content encoded as utf-8-sig.
    """
    stock_update_buf = io.StringIO()
    stock_fields = ["Category", "Part", "Qty"]
    stock_writer = csv.DictWriter(stock_update_buf, fieldnames=stock_fields)
    stock_writer.writeheader()

    for row in data:
        # Robustly handle potential string/int types from the UI
        current_stock = int(row.get("In Stock", 0))
        buy_qty = int(row.get("Buy Qty", 0))
        used_qty = int(row.get("BOM Qty", 0)) - int(row.get("Included", 0))

        # Calculate the resulting inventory state
        new_qty = (current_stock + buy_qty) - used_qty

        # Only write rows where stock remains; omit zero-qty items to keep the CSV clean
        if new_qty > 0:
            stock_writer.writerow(
                {"Category": row["Category"], "Part": row["Part"], "Qty": new_qty}
            )

    return stock_update_buf.getvalue().encode("utf-8-sig")


# Columns of the supplier cart file. Mouser's and Digi-Key's BOM upload pages
# ask which column holds what, so the names only need to be self-explanatory.
SUPPLIER_CART_COLS = ["Quantity", "Manufacturer Part Number", "Description"]

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


def generate_supplier_cart_csv(data: list[ShoppingListRow]) -> bytes:
    """Generates a cart file for a supplier's BOM upload page (Mouser, Digi-Key).

    One line per part to buy. Parts named by a part number carry it, so the
    supplier matches them directly; generic parts (resistors, capacitors,
    pots, hardware) carry a description to choose a match from.

    Args:
        data: The shopping list rows.

    Returns:
        The CSV content encoded as utf-8 (no BOM signature: upload pages
        read it as part of the first header).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(SUPPLIER_CART_COLS)
    for row in data:
        if int(row.get("Buy Qty", 0)) <= 0:
            continue
        category = ComponentCategory(row["Category"])
        part_number = manufacturer_part_number(category, row["Part"])
        description = row.get("Search Term") or row["Part"]
        writer.writerow(
            [row["Buy Qty"], part_number, f"{category.value}: {description}"]
        )
    return buf.getvalue().encode("utf-8")
