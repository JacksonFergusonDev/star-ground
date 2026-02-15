"""
Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

from collections import defaultdict
from typing import TypedDict


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
        refs: List of designators (e.g., ['R1', 'R2']).
        sources: Mapping of project names to the specific refs they contributed.
    """

    qty: int
    refs: list[str]
    sources: dict[str, list[str]]


# Type alias for the core data structure: Key ("Category | Value") -> Data
InventoryType = dict[str, PartData]


def create_empty_inventory() -> InventoryType:
    """
    Creates a standardized, empty inventory dictionary.

    Returns:
        A defaultdict initialized with zero quantity and empty lists.
    """
    return defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)})
