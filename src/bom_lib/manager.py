"""High-level inventory management and data mutation logic.

This module acts as the "Controller" for the BOM library. It handles:
- Core dictionary mutation (recording parts).
- Sorting and organizing the inventory for display.
- Calculating net purchasing needs (BOM vs. Stock).
- Renaming sources/projects.
"""

from decimal import Decimal

import pint

from src.bom_lib.enums import ComponentCategory
from src.bom_lib.types import (
    ComponentKey,
    Inventory,
    PartData,
)


def calculate_net_needs(bom: Inventory, stock: Inventory) -> Inventory:
    """Calculates the deficit between Required parts (BOM) and Owned parts (Stock).

    Args:
        bom: The requested build materials.
        stock: The user's current inventory.

    Returns:
        A new Inventory dictionary containing ONLY the items that need to be purchased.
        Quantities are set to `max(0, required - owned)`.
    """
    net_inv = Inventory()

    for key, data in bom.items():
        gross_needed = data["qty"]

        # Check stock
        stock_data = stock.get(key)
        in_stock = stock_data["qty"] if stock_data else 0

        # The Math: (Need - Have), floored at 0
        net_needed = max(0, gross_needed - in_stock)

        # Preserve metadata, but update Qty to the Net Need
        net_inv[key] = data.copy()
        net_inv[key]["qty"] = net_needed

    return net_inv


def sort_inventory(inventory: Inventory) -> list[tuple[ComponentKey, PartData]]:
    """Sorts the inventory for display.

    Sorting hierarchy:
    1. Category (defined by fixed rank).
    2. Electrical Value (numerical sort of Ohms/Farads).
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
        ComponentCategory.HARDWARE_MISC,
    ]
    # Map category to index for sorting efficiency
    pmap = {cat: i for i, cat in enumerate(order)}

    def sort_key(
        item: tuple[ComponentKey, PartData],
    ) -> tuple[int, Decimal, str]:
        key, data = item
        rank = pmap.get(key.category, 100)

        val_qty = data.get("val_qty")
        if isinstance(val_qty, pint.Quantity):
            mag = Decimal(str(val_qty.to_base_units().magnitude))
        elif isinstance(val_qty, Decimal):
            mag = val_qty
        else:
            mag = Decimal(0)

        return (rank, mag, key.value)

    return sorted(inventory.items(), key=sort_key)


def rename_source_in_inventory(
    inventory: Inventory, old_name: str, new_name: str
) -> None:
    """Updates the source key in the inventory (e.g., renaming a project).

    Args:
        inventory: The inventory to mutate.
        old_name: The existing source label.
        new_name: The new source label.
    """
    if old_name == new_name:
        return

    for part in inventory.values():
        if old_name in part["sources"]:
            part["sources"][new_name] = part["sources"].pop(old_name)


def serialize_inventory(inventory: Inventory) -> str:
    r"""Converts the inventory dict back into the standardized text format.

    e.g. {'Resistors | 10k': refs=['R1', 'R2']} -> "R1 10k\nR2 10k"

    Args:
        inventory: The populated inventory dictionary.

    Returns:
        A newline-separated string suitable for the 'Manual Input' text area.
    """
    lines = []

    # Use the existing sort logic in this module
    sorted_items = sort_inventory(inventory)

    for key, data in sorted_items:
        clean_val = key.value

        # If we have specific refs (R1, C1), list them individually
        if data["refs"]:
            for ref in data["refs"]:
                # Ignore generic hardware refs if they slipped in
                if ref != "HW":
                    lines.append(f"{ref} {clean_val}")
        else:
            # Fallback for things without refs (rare in presets)
            lines.append(f"{clean_val} (Qty: {data['qty']})")

    return "\n".join(lines)
