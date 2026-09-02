"""Tests for CSV exporters using typed ShoppingListRow."""

from src.bom_lib.types import ShoppingListRow
from src.exporters import generate_shopping_list_csv, generate_stock_update_csv


def test_generate_shopping_list_csv_standard() -> None:
    """Generates standard CSV without formulas and without stock columns."""
    rows: list[ShoppingListRow] = [
        {
            "Origin": "Circuit Board",
            "Category": "Resistors",
            "Part": "10k",
            "BOM Qty": 4,
            "Buy Qty": 10,
            "Notes": "1/4W Metal Film",
            "Search Term": "10k ohm 1/4w metal film",
            "Tayda_Link": "https://www.taydaelectronics.com/catalogsearch/result/?q=10k",
        }
    ]

    csv_bytes = generate_shopping_list_csv(rows, use_excel_formulas=False)
    decoded = csv_bytes.decode("utf-8-sig")

    assert (
        "Category,Part,BOM Qty,Buy Qty,Notes,Search Term,Tayda_Link,Origin" in decoded
    )
    assert (
        "Resistors,10k,4,10,1/4W Metal Film,10k ohm 1/4w metal film,https://www.taydaelectronics.com/catalogsearch/result/?q=10k,Circuit Board"
        in decoded
    )


def test_generate_shopping_list_csv_with_stock_and_formula() -> None:
    """Generates CSV with stock columns and Excel HYPERLINK formulas."""
    rows: list[ShoppingListRow] = [
        {
            "Origin": "Circuit Board",
            "Category": "Capacitors",
            "Part": "100n",
            "BOM Qty": 5,
            "In Stock": 2,
            "Net Need": 3,
            "Buy Qty": 10,
            "Notes": "Box Film",
            "Search Term": "100nF Box Film",
            "Tayda_Link": "https://www.taydaelectronics.com/catalogsearch/result/?q=100n",
        }
    ]

    csv_bytes = generate_shopping_list_csv(rows, use_excel_formulas=True)
    decoded = csv_bytes.decode("utf-8-sig")

    import csv
    import io

    reader = list(csv.reader(io.StringIO(decoded)))
    headers = reader[0]
    row_data = reader[1]

    assert "In Stock" in headers
    assert "Net Need" in headers
    tayda_idx = headers.index("Tayda_Link")
    assert (
        row_data[tayda_idx]
        == '=HYPERLINK("https://www.taydaelectronics.com/catalogsearch/result/?q=100n", "Buy")'
    )


def test_generate_stock_update_csv() -> None:
    """Calculates remaining stock and excludes non-positive remainder rows."""
    rows: list[ShoppingListRow] = [
        {
            "Origin": "Circuit Board",
            "Category": "Resistors",
            "Part": "10k",
            "BOM Qty": 4,
            "In Stock": 2,
            "Net Need": 2,
            "Buy Qty": 10,
            "Notes": "",
            "Search Term": "10k",
            "Tayda_Link": "",
        },
        {
            "Origin": "Circuit Board",
            "Category": "ICs",
            "Part": "TL072",
            "BOM Qty": 2,
            "In Stock": 2,
            "Net Need": 0,
            "Buy Qty": 0,
            "Notes": "",
            "Search Term": "TL072",
            "Tayda_Link": "",
        },
    ]

    # For 10k: (2 + 10) - 4 = 8 remaining -> should be included
    # For TL072: (2 + 0) - 2 = 0 remaining -> omitted
    csv_bytes = generate_stock_update_csv(rows)
    decoded = csv_bytes.decode("utf-8-sig")

    assert "Category,Part,Qty" in decoded
    assert "Resistors,10k,8" in decoded
    assert "TL072" not in decoded
