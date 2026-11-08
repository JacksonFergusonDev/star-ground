"""The master list's CSV files."""

import csv
import io
from dataclasses import replace
from pathlib import Path

import pytest

from star_ground.bom_lib.enums import ComponentCategory, ComponentOrigin
from star_ground.bom_lib.parser import parse_user_inventory
from star_ground.bom_lib.types import FORMULA_PREFIXES, ShoppingLine
from star_ground.exports.spreadsheets import (
    SHOPPING_LIST_COLUMNS,
    manufacturer_part_number,
    shopping_list_csv,
    stock_after_csv,
    supplier_cart_csv,
)


def _line(part: str = "10k", **overrides: object) -> ShoppingLine:
    line = ShoppingLine(
        origin=ComponentOrigin.CIRCUIT_BOARD,
        category=ComponentCategory.RESISTORS,
        part=part,
        bom_qty=1,
        included=0,
        in_stock=0,
        net_need=1,
        buy_qty=1,
        notes="",
        search_term=part,
        supplier_url="",
    )
    return replace(line, **overrides)  # type: ignore[arg-type]


def test_generate_shopping_list_csv_standard() -> None:
    """Writes one row per line under the shopping list's columns."""
    rows = [
        _line(
            "10k",
            bom_qty=4,
            net_need=4,
            buy_qty=10,
            notes="1/4W Metal Film",
            search_term="10k ohm 1/4w metal film",
            supplier_url="https://www.taydaelectronics.com/catalogsearch/result/?q=10k",
        )
    ]

    decoded = shopping_list_csv(rows).decode("utf-8-sig")

    assert decoded.splitlines() == [
        ",".join(SHOPPING_LIST_COLUMNS),
        "Resistors,10k,4,0,0,4,10,1/4W Metal Film,10k ohm 1/4w metal film,https://www.taydaelectronics.com/catalogsearch/result/?q=10k,Circuit Board",
    ]


def test_generate_shopping_list_csv_with_stock_and_formula() -> None:
    """Generates CSV with stock columns and Excel HYPERLINK formulas."""
    rows = [
        _line(
            "100n",
            category=ComponentCategory.CAPACITORS,
            bom_qty=5,
            in_stock=2,
            net_need=3,
            buy_qty=10,
            supplier_url="https://www.taydaelectronics.com/catalogsearch/result/?q=100n",
        )
    ]

    decoded = shopping_list_csv(rows, excel_links=True).decode("utf-8-sig")

    headers, row_data = list(csv.reader(io.StringIO(decoded)))
    assert row_data[headers.index("In stock")] == "2"
    assert row_data[headers.index("Still needed")] == "3"
    assert (
        row_data[headers.index("Tayda link")]
        == '=HYPERLINK("https://www.taydaelectronics.com/catalogsearch/result/?q=100n", "Buy")'
    )


def test_stock_after_csv() -> None:
    """Calculates remaining stock and excludes non-positive remainder rows."""
    rows = [
        _line("10k", bom_qty=4, in_stock=2, net_need=2, buy_qty=10),
        _line(
            "TL072",
            category=ComponentCategory.ICS,
            bom_qty=2,
            in_stock=2,
            net_need=0,
            buy_qty=0,
        ),
    ]

    # For 10k: (2 + 10) - 4 = 8 remaining -> should be included
    # For TL072: (2 + 0) - 2 = 0 remaining -> omitted
    decoded = stock_after_csv(rows).decode("utf-8-sig")

    assert "Category,Part,Qty" in decoded
    assert "Resistors,10k,8" in decoded
    assert "TL072" not in decoded


def test_stock_update_does_not_spend_stock_on_included_parts() -> None:
    """A part that comes with the PCB is used without coming from stock."""
    rows = [
        _line(
            "24LC32A",
            category=ComponentCategory.ICS,
            bom_qty=2,
            included=1,
            in_stock=3,
            net_need=0,
            buy_qty=0,
        )
    ]
    assert "ICs,24LC32A,2" in stock_after_csv(rows).decode("utf-8-sig")


def _row(
    category: ComponentCategory, part: str, buy: int, search: str = ""
) -> ShoppingLine:
    return _line(
        part,
        category=category,
        bom_qty=buy,
        net_need=buy,
        buy_qty=buy,
        search_term=search or part,
    )


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
        _row(ComponentCategory.ICS, "TL072", 2),
        _row(ComponentCategory.RESISTORS, "10k", 10, "10k ohm 1/4w metal film"),
        _row(ComponentCategory.CAPACITORS, "100n", 0),  # in stock: nothing to buy
    ]
    text = supplier_cart_csv(rows).decode("utf-8")
    assert text.splitlines() == [
        "Quantity,Manufacturer Part Number,Description",
        "2,TL072,ICs: TL072",
        "10,,Resistors: 10k ohm 1/4w metal film",
    ]


def _shopping_row(part: str, **overrides: object) -> ShoppingLine:
    line = _line(
        part,
        category=ComponentCategory.ICS,
        supplier_url="https://www.taydaelectronics.com/catalogsearch/result/?q=x",
    )
    return replace(line, **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("part", ["=1+1", "+cmd|calc", "-2+3", "@SUM(A1)"])
def test_document_text_never_becomes_a_spreadsheet_formula(part: str) -> None:
    rows = [_shopping_row(part)]

    shopping = shopping_list_csv(rows, excel_links=True)
    stock = stock_after_csv([_shopping_row(part, buy_qty=2)])
    cart = supplier_cart_csv(rows)

    for csv_bytes in (shopping, stock, cart):
        cells = [
            cell
            for line in csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig")))
            for cell in line
        ]
        formulas = [c for c in cells if c.startswith(FORMULA_PREFIXES)]
        assert all(c.startswith('=HYPERLINK("https://') for c in formulas)
        assert any(part in c for c in cells)
    # The link the app builds is still a formula.
    assert '"=HYPERLINK(""https://' in shopping.decode("utf-8-sig")


def test_the_stock_file_reads_back_a_guarded_part(tmp_path: Path) -> None:
    stock_csv = stock_after_csv(
        [_shopping_row("-2+3", category=ComponentCategory.TRANSISTORS, buy_qty=3)]
    )
    path = tmp_path / "stock.csv"
    path.write_bytes(stock_csv)

    stock = parse_user_inventory(str(path))

    assert [k.value for k in stock] == ["-2+3"]
