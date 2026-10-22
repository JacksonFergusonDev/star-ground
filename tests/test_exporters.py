"""Tests for CSV exporters using typed ShoppingListRow."""

import pytest

from src.bom_lib.enums import ComponentCategory, ComponentOrigin
from src.bom_lib.types import ShoppingListRow
from src.exporters import (
    BASE_SHOPPING_LIST_COLS,
    generate_shopping_list_csv,
    generate_stock_update_csv,
    generate_supplier_cart_csv,
    get_shopping_list_columns,
    manufacturer_part_number,
)


def test_get_shopping_list_columns() -> None:
    """Verifies column ordering for base, with stock, and with search term."""
    # Base layout
    base_cols = get_shopping_list_columns(has_stock=False, include_search_term=False)
    assert base_cols == BASE_SHOPPING_LIST_COLS
    assert "In Stock" not in base_cols
    assert "Search Term" not in base_cols

    # With stock
    stock_cols = get_shopping_list_columns(has_stock=True, include_search_term=False)
    assert stock_cols[3:5] == ["In Stock", "Net Need"]
    assert "Search Term" not in stock_cols

    # With search term and stock
    full_cols = get_shopping_list_columns(has_stock=True, include_search_term=True)
    assert full_cols[3:5] == ["In Stock", "Net Need"]
    assert "Search Term" in full_cols


def test_generate_shopping_list_csv_standard() -> None:
    """Generates standard CSV without formulas and without stock columns."""
    rows: list[ShoppingListRow] = [
        {
            "Origin": ComponentOrigin.CIRCUIT_BOARD,
            "Category": "Resistors",
            "Part": "10k",
            "BOM Qty": 4,
            "Included": 0,
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
            "Origin": ComponentOrigin.CIRCUIT_BOARD,
            "Category": "Capacitors",
            "Part": "100n",
            "BOM Qty": 5,
            "Included": 0,
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
            "Origin": ComponentOrigin.CIRCUIT_BOARD,
            "Category": "Resistors",
            "Part": "10k",
            "BOM Qty": 4,
            "Included": 0,
            "In Stock": 2,
            "Net Need": 2,
            "Buy Qty": 10,
            "Notes": "",
            "Search Term": "10k",
            "Tayda_Link": "",
        },
        {
            "Origin": ComponentOrigin.CIRCUIT_BOARD,
            "Category": "ICs",
            "Part": "TL072",
            "BOM Qty": 2,
            "Included": 0,
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


def test_stock_update_does_not_spend_stock_on_included_parts() -> None:
    """A part that comes with the PCB is used without coming from stock."""
    rows: list[ShoppingListRow] = [
        {
            "Origin": ComponentOrigin.CIRCUIT_BOARD,
            "Category": "ICs",
            "Part": "24LC32A",
            "BOM Qty": 2,
            "Included": 1,
            "In Stock": 3,
            "Net Need": 0,
            "Buy Qty": 0,
            "Notes": "",
            "Search Term": "24LC32A",
            "Tayda_Link": "",
        },
    ]
    assert "ICs,24LC32A,2" in generate_stock_update_csv(rows).decode("utf-8-sig")


def _row(category: str, part: str, buy: int, search: str = "") -> ShoppingListRow:
    return {
        "Origin": ComponentOrigin.CIRCUIT_BOARD,
        "Category": category,
        "Part": part,
        "BOM Qty": buy,
        "Included": 0,
        "In Stock": 0,
        "Net Need": buy,
        "Buy Qty": buy,
        "Notes": "",
        "Search Term": search or part,
        "Tayda_Link": "",
    }


@pytest.mark.parametrize(
    ("category", "part", "expected"),
    [
        (ComponentCategory.ICS, "TL072", "TL072"),
        (ComponentCategory.DIODES, "1N4148", "1N4148"),
        (ComponentCategory.TRANSISTORS, "2N5088", "2N5088"),
        (ComponentCategory.RELAYS, "TQ2-L-5V", "TQ2-L-5V"),
        (ComponentCategory.TRANSFORMERS, "42TM018", "42TM018"),
        (ComponentCategory.DIODES, "LED", ""),
        (ComponentCategory.DIODES, "3mm Red LED", ""),
        (ComponentCategory.RESISTORS, "10k", ""),
        (ComponentCategory.CAPACITORS, "100n", ""),
        (ComponentCategory.TRANSISTORS, "Germanium PNP", ""),
    ],
)
def test_only_real_part_numbers_are_given_to_the_supplier(
    category: ComponentCategory, part: str, expected: str
) -> None:
    assert manufacturer_part_number(category, part) == expected


def test_the_supplier_cart_lists_what_to_buy() -> None:
    rows = [
        _row("ICs", "TL072", 2),
        _row("Resistors", "10k", 10, "10k ohm 1/4w metal film"),
        _row("Capacitors", "100n", 0),  # in stock: nothing to buy
    ]
    text = generate_supplier_cart_csv(rows).decode("utf-8")
    assert text.splitlines() == [
        "Quantity,Manufacturer Part Number,Description",
        "2,TL072,ICs: TL072",
        "10,,Resistors: 10k ohm 1/4w metal film",
    ]
