"""Tayda Electronics' SKUs for the parts the shopping list recommends.

Tayda's Quick Order page (https://www.taydaelectronics.com/quick-order/)
fills a cart from `SKU, QTY` lines, or a CSV with a `sku,qty` header. This
module reads `data/tayda.csv`, which maps a shopping line's search term
(`sourcing.generate_search_term`, the product the list recommends: '10k ohm
1/4w metal film') to Tayda's product. Each row is checked by hand on Tayda's
site: Tayda answers no script, so the table is never scraped.

A product's `step` is the quantity Tayda sells it in multiples of. Below it
Tayda quietly raises the quantity, but a quantity between multiples (12
resistors sold in tens) fails at checkout, so the cart rounds up to it.
"""

import csv
import math
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

TABLE = Path(__file__).parent / "data" / "tayda.csv"
TABLE_COLUMNS = ("term", "sku", "step", "url", "title", "checked")

_SKU = re.compile(r"[A-Z]-\d+")
_PRODUCT_URL = re.compile(r"https://www\.taydaelectronics\.com/[a-z0-9-]+\.html")


@dataclass(frozen=True, slots=True)
class TaydaProduct:
    """One of Tayda's products.

    Attributes:
        sku: Tayda's SKU ('A-2203').
        step: The quantity it's sold in multiples of (10 for resistors).
        url: Its product page, or '' when it hasn't been checked.
        title: Its name on Tayda, to check the match by eye.
        checked: When the row was last checked on Tayda's site.
    """

    sku: str
    step: int
    url: str
    title: str
    checked: date


def term_key(term: str) -> str:
    """A search term as the table matches it: lower case, single spaces."""
    return " ".join(term.split()).casefold()


@cache
def products() -> dict[str, TaydaProduct]:
    """The table, by `term_key`.

    Returns:
        Each term's product.

    Raises:
        ValueError: The table is malformed: wrong columns, a repeated term,
            or a SKU, step, URL or date that doesn't parse.
    """
    with TABLE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != TABLE_COLUMNS:
            raise ValueError(f"{TABLE.name}: columns must be {TABLE_COLUMNS}")
        table: dict[str, TaydaProduct] = {}
        for line, row in enumerate(reader, start=2):
            key = term_key(row["term"])
            if not key or key in table:
                raise ValueError(f"{TABLE.name}:{line}: empty or repeated term")
            if not _SKU.fullmatch(row["sku"]):
                raise ValueError(f"{TABLE.name}:{line}: bad SKU {row['sku']!r}")
            if row["url"] and not _PRODUCT_URL.fullmatch(row["url"]):
                raise ValueError(f"{TABLE.name}:{line}: bad URL {row['url']!r}")
            step = int(row["step"])
            if step < 1:
                raise ValueError(f"{TABLE.name}:{line}: step must be at least 1")
            table[key] = TaydaProduct(
                sku=row["sku"],
                step=step,
                url=row["url"],
                title=row["title"],
                checked=date.fromisoformat(row["checked"]),
            )
    return table


def tayda_product(search_term: str) -> TaydaProduct | None:
    """Tayda's product for a shopping line's search term, if the table has one.

    Args:
        search_term: The line's search term ('10k ohm 1/4w metal film').

    Returns:
        The product, or None for a part the table doesn't cover yet.
    """
    return products().get(term_key(search_term))


def cart_qty(buy_qty: int, step: int) -> int:
    """How many to put in the cart: the buy quantity, up to Tayda's multiple.

    Args:
        buy_qty: How many the shopping list says to buy.
        step: The multiple Tayda sells the product in.

    Returns:
        The smallest multiple of `step` at least `buy_qty`.
    """
    return math.ceil(buy_qty / step) * step
