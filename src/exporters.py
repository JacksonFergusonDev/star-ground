import csv
import io
from typing import Any


def generate_shopping_list_csv(
    data: list[dict[str, Any]], use_excel_formulas: bool = False
) -> bytes:
    """
    Generates a CSV file for the shopping list.

    Constructs a UTF-8 encoded CSV string (with BOM signature) suitable for
    download. It adapts the columns dynamically based on whether 'Net Need'
    data is available (i.e., if stock was checked).

    Args:
        data (list[dict]): The list of row dictionaries to write.
        use_excel_formulas (bool):  If True, formats URLs as Excel `=HYPERLINK()` formulas.
                                    If False, writes raw URL strings.

    Returns:
        bytes: The CSV content encoded as utf-8-sig.
    """
    csv_buf = io.StringIO()

    # Define columns based on data presence
    fields = [
        "Category",
        "Part",
        "BOM Qty",
        "Buy Qty",
        "Notes",
        "Search Term",
        "Tayda_Link",
        "Origin",
    ]

    # Inject Stock columns if they exist in the dataset
    if data and "Net Need" in data[0]:
        fields[3:3] = ["In Stock", "Net Need"]

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


def generate_stock_update_csv(data: list[dict[str, Any]]) -> bytes:
    """
    Calculates updated stock levels and generates a CSV import file.

    Logic:
        New Stock = (Current Stock + Buy Qty) - Used Qty

    This file is intended to be re-uploaded by the user next time they use
    the app, closing the logistics loop.

    Args:
        data (list[dict]): The processed master list data.

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
        used_qty = int(row.get("BOM Qty", 0))

        # Calculate the resulting inventory state
        new_qty = (current_stock + buy_qty) - used_qty

        # Only write rows where stock remains; omit zero-qty items to keep the CSV clean
        if new_qty > 0:
            stock_writer.writerow(
                {"Category": row["Category"], "Part": row["Part"], "Qty": new_qty}
            )

    return stock_update_buf.getvalue().encode("utf-8-sig")
