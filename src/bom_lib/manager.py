"""
High-level inventory management and data mutation logic.

This module acts as the "Controller" for the BOM library. It handles:
- Core dictionary mutation (recording parts).
- Sorting and organizing the inventory for display.
- Calculating net purchasing needs (BOM vs. Stock).
- Renaming sources/projects.
"""

from collections import defaultdict

from src.bom_lib.types import InventoryType, PartData
from src.bom_lib.utils import parse_value_to_float


def _record_part(
    inventory: InventoryType, source: str, key: str, ref: str, qty: int = 1
) -> None:
    """
    Low-level helper to update the inventory dictionary.

    Args:
        inventory: The inventory data structure.
        source: Source identifier (e.g., "Big Muff").
        key: The unique component key (e.g., "Resistors | 10k").
        ref: The reference designator (e.g., "R1").
        qty: Quantity to add.
    """
    inventory[key]["qty"] += qty
    # Only track refs if provided (avoids empty strings for stock items)
    if ref:
        inventory[key]["refs"].append(ref)
        inventory[key]["sources"][source].append(ref)


def calculate_net_needs(bom: InventoryType, stock: InventoryType) -> InventoryType:
    """
    Calculates the deficit between Required parts (BOM) and Owned parts (Stock).

    Args:
        bom: The requested build materials.
        stock: The user's current inventory.

    Returns:
        A new InventoryType containing ONLY the items that need to be purchased.
        Quantities are set to `max(0, required - owned)`.
    """
    net_inv: InventoryType = defaultdict(
        lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}
    )

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


def sort_inventory(inventory: InventoryType) -> list[tuple[str, PartData]]:
    """
    Sorts the inventory for display.

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
        "PCB",
        "ICs",
        "Crystals/Oscillators",
        "Optoelectronics",
        "Transistors",
        "Diodes",
        "Potentiometers",
        "Switches",
        "Capacitors",
        "Resistors",
        "Hardware/Misc",
    ]
    # Map name to index for sorting efficiency
    pmap = {name: i for i, name in enumerate(order)}

    def sort_key(item: tuple[str, PartData]) -> tuple[int, float, str]:
        key = item[0]
        if " | " not in key:
            return (999, 0.0, key)

        cat, val = key.split(" | ", 1)
        rank = pmap.get(cat, 100)

        # Parse value for sorting
        fval = parse_value_to_float(val)
        if fval is None:
            fval = 0.0

        return (rank, fval, val)

    return sorted(inventory.items(), key=sort_key)


def rename_source_in_inventory(
    inventory: InventoryType, old_name: str, new_name: str
) -> None:
    """
    Updates the source key in the inventory (e.g., renaming a project).

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


def serialize_inventory(inventory: InventoryType) -> str:
    """
    Converts the inventory dict back into the standardized text format.
    e.g. {'Resistors | 10k': refs=['R1', 'R2']} -> "R1 10k\\nR2 10k"

    Args:
        inventory: The populated inventory dictionary.

    Returns:
        A newline-separated string suitable for the 'Manual Input' text area.
    """
    lines = []

    def get_val(key: str) -> str:
        if " | " in key:
            return key.split(" | ", 1)[1]
        return key

    # Use the existing sort logic in this module
    sorted_items = sort_inventory(inventory)

    for key, data in sorted_items:
        clean_val = get_val(key)

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
