"""High-level inventory management and data mutation logic.

This module acts as the "Controller" for the BOM library. It handles:
- Core dictionary mutation (recording parts).
- Sorting and organizing the inventory for display.
- Calculating net purchasing needs (BOM vs. Stock).
- Renaming sources/projects.
"""

import copy
import re
from dataclasses import replace
from decimal import Decimal

from star_ground.bom_lib.classifier import key_quantity
from star_ground.bom_lib.constants import INJECTED_SUFFIX
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


def serialize_inventory(inventory: Inventory) -> str:
    r"""Converts the inventory dict back into the standardized text format.

    e.g. {'Resistors | 10k': refs=['R1', 'R2']} -> "R1 10k\nR2 10k"

    Parts the parser injected (IC sockets, refs like 'IC1 (Inj)') are left
    out: parsing the text injects them again. So is the hardware kit, which
    no document lists.

    Args:
        inventory: The populated inventory dictionary.

    Returns:
        A newline-separated string suitable for the 'Manual Input' text area.
    """
    lines = []

    # Use the existing sort logic in this module
    sorted_items = sort_inventory(inventory)

    for key, data in sorted_items:
        if data.hardware and not data.sources:
            continue
        clean_val = key.value

        # If we have specific refs (R1, C1), list them individually
        if data.refs:
            for ref in data.refs:
                if not ref.endswith(INJECTED_SUFFIX):
                    lines.append(f"{ref} {clean_val}")
        else:
            # Fallback for parts without refs
            lines.append(f"{clean_val} (Qty: {data.qty})")

    return "\n".join(lines)
