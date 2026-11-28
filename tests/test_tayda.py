"""Tayda's SKU table and its cart quantities."""

import pytest

from star_ground.bom_lib import tayda
from star_ground.bom_lib.chips import chip_pins, is_charge_pump


def test_the_table_reads() -> None:
    # Every row parses: columns, SKUs, steps, URLs and dates (see `products`).
    assert tayda.products()


def test_a_term_matches_whatever_its_case_and_spacing() -> None:
    product = tayda.tayda_product("10K  Ohm 1/4W metal film")
    assert product is not None
    assert product.sku == "A-2203"
    assert tayda.tayda_product("10k ohm 1/2w metal film") is None


@pytest.mark.parametrize(
    ("buy", "step", "qty"),
    [(1, 1, 1), (12, 1, 12), (2, 10, 10), (12, 10, 20), (20, 10, 20)],
)
def test_the_cart_rounds_up_to_tayda_s_multiple(buy: int, step: int, qty: int) -> None:
    assert tayda.cart_qty(buy, step) == qty


@pytest.mark.parametrize(
    ("chip", "pins"),
    [
        ("TL072CP", 8),
        ("JRC4558D", 8),
        ("NE 5532", 8),
        ("7660SCPAZ", 8),
        ("TL074", 14),
        ("JRC4741D", 14),
        ("CD40106BE", 14),
        ("CD4049UBE", 16),
        ("PT2399", 16),
        ("LM13700N", 16),
        ("TL0721", None),
        ("FV-1", None),
        ("Dual Opamp", None),
    ],
)
def test_a_chip_s_pins_come_from_its_part_number(chip: str, pins: int | None) -> None:
    assert chip_pins(chip) == pins


@pytest.mark.parametrize(
    ("chip", "pump"),
    [("TC1044SCPA", True), ("ICL7660S", True), ("LT1054", True), ("TL072", False)],
)
def test_charge_pumps_are_known_by_part_number(chip: str, pump: bool) -> None:
    assert is_charge_pump(chip) is pump
