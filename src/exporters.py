import csv
import io
from typing import Any


def generate_shopping_list_csv(
    data: list[dict[str, Any]], use_excel_formulas: bool = False
) -> bytes:
    """Generates the shopping list CSV bytes."""
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
    if data and "Net Need" in data[0]:
        fields[3:3] = ["In Stock", "Net Need"]

    writer = csv.DictWriter(csv_buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()

    rows_to_write = []
    for row in data:
        if use_excel_formulas and row.get("Tayda_Link"):
            clean_row = row.copy()
            clean_row["Tayda_Link"] = f'=HYPERLINK("{row["Tayda_Link"]}", "Buy")'
            rows_to_write.append(clean_row)
        else:
            rows_to_write.append(row)

    writer.writerows(rows_to_write)
    return csv_buf.getvalue().encode("utf-8-sig")


def generate_stock_update_csv(data: list[dict[str, Any]]) -> bytes:
    """Calculates new stock levels and returns CSV bytes."""
    stock_update_buf = io.StringIO()
    stock_fields = ["Category", "Part", "Qty"]
    stock_writer = csv.DictWriter(stock_update_buf, fieldnames=stock_fields)
    stock_writer.writeheader()

    for row in data:
        # Logic: New Stock = (Old Stock + Buy Qty) - Used Qty
        current_stock = int(row.get("In Stock", 0))
        buy_qty = int(row.get("Buy Qty", 0))
        used_qty = int(row.get("BOM Qty", 0))

        new_qty = (current_stock + buy_qty) - used_qty

        if new_qty > 0:
            stock_writer.writerow(
                {"Category": row["Category"], "Part": row["Part"], "Qty": new_qty}
            )

    return stock_update_buf.getvalue().encode("utf-8-sig")
