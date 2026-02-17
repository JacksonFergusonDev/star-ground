"""
Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

from collections import UserDict, defaultdict
from typing import TypedDict

from src.bom_lib import parse_value_to_float


class StatsDict(TypedDict):
    """
    Tracking metrics and errors for a single ingestion session.

    Attributes:
        lines_read: Total lines processed from the source file.
        parts_found: Number of valid component matches extracted.
        residuals: Lines that were rejected/skipped during parsing.
        extracted_title: Heuristic guess at the project title (PDF only).
        seen_refs: Set of references seen so far to prevent duplicate counting.
    """

    lines_read: int
    parts_found: int
    residuals: list[str]
    extracted_title: str | None
    seen_refs: set[str]
    errors: list[str]


class PartData(TypedDict):
    """
    Structure representing a specific component's aggregate data.

    Attributes:
        qty: Total quantity required across all projects.
        val_float: The cached numeric value of the component (e.g. 1000.0 for '1k').
                   None if the value is non-numeric (e.g. 'TL072').
        refs: List of designators (e.g., ['R1', 'R2']).
        sources: Mapping of project names to the specific refs they contributed.
    """

    qty: int
    val_float: float | None
    refs: list[str]
    sources: dict[str, list[str]]


class Inventory(UserDict):
    """
    Concrete class for managing component inventory.

    Encapsulates storage, mutation, and aggregation logic to prevent
    invalid state transitions (e.g., assigning string to quantity).
    """

    def __init__(self, data=None):
        super().__init__(data)
        # Ensure default factory behavior for new keys
        if self.data is None:
            self.data = {}

    def __missing__(self, key: str) -> PartData:
        """Default factory for new parts."""
        value: PartData = {
            "qty": 0,
            "val_float": None,
            "refs": [],
            "sources": defaultdict(list),
        }
        self.data[key] = value
        return value

    def add_part(self, source: str, key: str, ref: str, qty: int = 1) -> None:
        """
        Records a part in the inventory.

        Args:
            source: Source identifier (e.g., "Big Muff").
            key: The unique component key (e.g., "Resistors | 10k").
            ref: The reference designator (e.g., "R1").
            qty: Quantity to add.
        """
        part = self[key]

        # Initialize cached float if this is a new part entry
        if part["qty"] == 0:
            if " | " in key:
                _, val_str = key.split(" | ", 1)
                part["val_float"] = parse_value_to_float(val_str)
            else:
                part["val_float"] = None

        part["qty"] += qty

        if ref:
            part["refs"].append(ref)
            part["sources"][source].append(ref)

    def merge(self, other: "Inventory", multiplier: int = 1) -> None:
        """
        Merges another inventory into this one.

        Args:
            other: The Inventory object to merge in.
            multiplier: Multiplication factor for the incoming inventory quantities.
        """
        for key, data in other.items():
            self[key]["qty"] += data["qty"] * multiplier
            self[key]["refs"].extend(data["refs"])
            for src, refs in data["sources"].items():
                self[key]["sources"][src].extend(refs * multiplier)


# Update Type Alias to point to new Class for backward compatibility checks
InventoryType = Inventory


def create_empty_inventory() -> Inventory:
    """Factory function to return new Inventory instance."""
    return Inventory()
