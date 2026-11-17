"""Whole-inventory operations: ordering, net needs and relabelling.

This module handles:
- Sorting and organizing the inventory for display.
- Calculating net purchasing needs (BOM vs. Stock).
- Matching the builder's stock to the parts lists' values (`match_stock`).
- Relabelling a project's parts (`rename_source`).
"""

import copy
import re
from dataclasses import replace
from decimal import Decimal

from star_ground.bom_lib.classifier import key_quantity
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.types import (
    ComponentKey,
    Inventory,
    PartData,
)
from star_ground.bom_lib.units import Value, ohms
from star_ground.bom_lib.utils import parse_value_to_decimal

# A pot's resistance, with a taper letter before or after it: 'B100K',
# '100kB', '100K-A'. A body size ('16mm B10K') is no resistance.
POT_RESISTANCE = re.compile(
    r"(?<![A-Z0-9])(?!\d+(?:\.\d+)?\s?MM\b)"
    r"([ABCWG])?(\d+(?:\.\d+)?[KM]?\d*)(?:-?([ABCWG])(?![A-Z]))?"
)


def pot_resistance(value: str) -> Value | None:
    """A pot's resistance, read past its taper code and any words.

    Args:
        value: The pot's value ('B100K', '100kB', '500k Rev. Log', 'Wah pot').

    Returns:
        The resistance in ohms, or None when the value gives none.
    """
    match = POT_RESISTANCE.search(value.upper())
    dec = parse_value_to_decimal(match.group(2)) if match else None
    return None if dec is None else ohms(dec)


# A capacitor's voltage rating, as a parts list or a builder writes it:
# '25V', '(25V minimum)' (`capacitor_notes`), '6.3 V'.
_RATING = re.compile(
    r"\(\s*(\d+(?:\.\d+)?)\s*V minimum\s*\)|\b(\d+(?:\.\d+)?)\s*V\b", re.I
)


def _rated(value: str) -> tuple[str, Decimal | None]:
    """A capacitor's value without its voltage rating, and the rating."""
    match = _RATING.search(value)
    if match is None:
        return value.casefold(), None
    rest = " ".join(f"{value[: match.start()]} {value[match.end() :]}".split())
    return rest.casefold(), Decimal(match.group(1) or match.group(2))


def _covers(stocked: ComponentKey, listed: ComponentKey) -> bool:
    """Whether a stocked capacitor will do for a listed one of another rating.

    The same capacitance and kind will: a builder who counted '10u' has the
    '10u (25V minimum)' a list asks for. A stocked rating, when one is
    written, must meet the listed one.
    """
    if not stocked.category == listed.category == ComponentCategory.CAPACITORS:
        return False
    have, have_volts = _rated(stocked.value)
    need, need_volts = _rated(listed.value)
    return have == need and (
        have_volts is None or need_volts is None or have_volts >= need_volts
    )


def match_stock(bom: Inventory, stock: Inventory) -> Inventory:
    """The stock, with each part written as the parts lists write it.

    A builder types values their own way: 'a100k' for a parts list's 'A100K',
    'tl072' for 'TL072', and '10u' for a list's '10u (25V minimum)'.
    Resistors and capacitors are already normalized as the stock is read;
    a value that differs from a listed part only in case is that part, and a
    capacitor differing only in its rating counts for it when its own rating,
    if written, is enough. A stocked part that will do for several listed
    ones fills the one written like it first, then the others, as far as
    each needs it; what's left stays with the first. Parts no list names
    keep their own keys.

    Args:
        bom: The parts to build.
        stock: The builder's stock, as read.

    Returns:
        A new inventory holding the same quantities.
    """
    wanted = {key: data.qty - data.included for key, data in bom.items()}
    matched = Inventory()
    for key, data in stock.items():
        spelled = bom.spelled(key)
        targets = ([spelled] if spelled in bom else []) + [
            k for k in bom if k != spelled and _covers(key, k)
        ]
        left = data.qty
        for target in targets:
            given = min(left, max(0, wanted[target]))
            wanted[target] -= given
            matched[target].qty += given
            left -= given
        if left:
            matched[targets[0] if targets else key].qty += left
    return matched


def calculate_net_needs(bom: Inventory, stock: Inventory) -> Inventory:
    """Calculates the deficit between Required parts (BOM) and Owned parts (Stock).

    Parts that come with a PCB (`PartData.included`) are not needed either.

    Args:
        bom: The requested build materials.
        stock: The user's current inventory.

    Returns:
        A new Inventory dictionary containing ONLY the items that need to be purchased.
        Quantities are set to `max(0, required - owned)`.
    """
    net_inv = Inventory()

    for key, data in bom.items():
        gross_needed = data.qty - data.included

        # Check stock
        stock_data = stock.get(key)
        in_stock = stock_data.qty if stock_data else 0

        # The Math: (Need - Have), floored at 0
        net_needed = max(0, gross_needed - in_stock)

        # Preserve metadata, but update Qty to the Net Need
        net_inv[key] = replace(data, qty=net_needed)

    return net_inv


def sort_inventory(inventory: Inventory) -> list[tuple[ComponentKey, PartData]]:
    """Sorts the inventory for display.

    Sorting hierarchy:
    1. Category (defined by fixed rank).
    2. Electrical Value (numerical sort of Ohms/Farads; a pot by its
       resistance, whatever its taper code: 'B1K' before '10kB').
    3. Alphabetical (fallback).

    Args:
        inventory: The unsorted inventory dictionary.

    Returns:
        A list of (key, data) tuples sorted by category and value.
    """
    order = [
        ComponentCategory.PCB,
        ComponentCategory.ICS,
        ComponentCategory.CRYSTALS_OSCILLATORS,
        ComponentCategory.OPTOELECTRONICS,
        ComponentCategory.TRANSISTORS,
        ComponentCategory.DIODES,
        ComponentCategory.POTENTIOMETERS,
        ComponentCategory.SWITCHES,
        ComponentCategory.CAPACITORS,
        ComponentCategory.RESISTORS,
        ComponentCategory.INDUCTORS,
        ComponentCategory.TRANSFORMERS,
        ComponentCategory.RELAYS,
        ComponentCategory.HARDWARE_MISC,
    ]
    # Map category to index for sorting efficiency
    pmap = {cat: i for i, cat in enumerate(order)}

    def sort_key(
        item: tuple[ComponentKey, PartData],
    ) -> tuple[int, Decimal, str]:
        key, _ = item
        rank = pmap.get(key.category, 100)

        val_qty = (
            pot_resistance(key.value)
            if key.category == ComponentCategory.POTENTIOMETERS
            else key_quantity(key)
        )
        if isinstance(val_qty, Value):
            mag = val_qty.magnitude
        elif isinstance(val_qty, Decimal):
            mag = val_qty
        else:
            mag = Decimal(0)

        return (rank, mag, key.value)

    return sorted(inventory.items(), key=sort_key)


def rename_source(inventory: Inventory, old_name: str, new_name: str) -> Inventory:
    """A copy of the inventory with one source under a new label.

    Args:
        inventory: The inventory, left unchanged.
        old_name: The existing source label.
        new_name: The new source label.

    Returns:
        The renamed copy.
    """
    renamed = copy.deepcopy(inventory)
    if old_name == new_name:
        return renamed
    for part in renamed.values():
        if old_name in part.sources:
            part.sources[new_name] = part.sources.pop(old_name)
        if old_name in part.counted:
            part.counted[new_name] = part.counted.pop(old_name)
        if old_name in part.multiples:
            part.multiples[new_name] = part.multiples.pop(old_name)
    return renamed
