"""Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

import uuid
from collections import UserDict, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple, NotRequired, TypedDict

import pint

from src.bom_lib.enums import ComponentCategory, InputMethod


@dataclass
class ProjectSlot:
    """Represents the UI state for a single pedal project slot.

    Shared between the Streamlit frontend and PDF generation backend.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    method: InputMethod = InputMethod.PASTE_TEXT
    count: int = 1
    data: Any = None

    # Cache fields
    last_loaded_preset: str | None = None
    cached_pdf_bytes: bytes | None = None
    source_path: str | None = None
    locked_name: str | None = None


class StatsDict(TypedDict):
    """Tracking metrics and errors for a single ingestion session.

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
    """Structure representing a specific component's aggregate data.

    Attributes:
        qty: Total quantity required across all projects.
        val_qty: The cached physical quantity or Decimal value of the component
                 (e.g. 10000 * ureg.ohm for '10k' Resistors).
                 None if the value is non-numeric (e.g. 'TL072').
        refs: List of designators (e.g., ['R1', 'R2']).
        sources: Mapping of project names to the specific refs they contributed.
    """

    qty: int
    val_qty: pint.Quantity[Any] | Decimal | None
    refs: list[str]
    sources: dict[str, list[str]]


class AlternativeSpec(NamedTuple):
    """Specification for component substitutions and tonal alternatives.

    Attributes:
        name: Name of the alternative component (e.g., 'OPA2134').
        profile: Sonic profile or character description (e.g., 'Hi-Fi / Studio Clean').
        justification: Technical rationale or specification detail.
    """

    name: str
    profile: str
    justification: str | None = None


class ChecklistPart(TypedDict):
    """Component checklist item for PDF Field Manual generation.

    Attributes:
        category: Component category name (e.g., 'Resistors', 'Capacitors').
        value: Cleaned component value string (e.g., '10k', 'TL072').
        qty: Total count of this part in the project.
        refs: List of designators for this component (e.g., ['R1', 'R2']).
        notes: Build annotations (e.g. '[!] Check Size').
        polarized: True if component requires orientation verification.
    """

    category: str
    value: str
    qty: int
    refs: list[str]
    notes: str
    polarized: bool


ShoppingListRow = TypedDict(
    "ShoppingListRow",
    {
        "Origin": str,
        "Category": str,
        "Part": str,
        "BOM Qty": int,
        "In Stock": NotRequired[int],
        "Net Need": NotRequired[int],
        "Buy Qty": int,
        "Notes": str,
        "Search Term": str,
        "Tayda_Link": str,
    },
)


class Inventory(UserDict[str, PartData]):
    """Concrete class for managing component inventory.

    Encapsulates storage, mutation, and aggregation logic to prevent
    invalid state transitions (e.g., assigning string to quantity).
    """

    def __init__(self, data: dict[str, PartData] | None = None) -> None:
        super().__init__(data)
        # Ensure default factory behavior for new keys
        if self.data is None:
            self.data = {}

    def __missing__(self, key: str) -> PartData:
        """Default factory for new parts."""
        value: PartData = {
            "qty": 0,
            "val_qty": None,
            "refs": [],
            "sources": defaultdict(list),
        }
        self.data[key] = value
        return value

    def add_part(self, source: str, key: str, ref: str, qty: int = 1) -> None:
        """Records a part in the inventory.

        Args:
            source: Source identifier (e.g., "Big Muff").
            key: The unique component key (e.g., "Resistors | 10k").
            ref: The reference designator (e.g., "R1").
            qty: Quantity to add.
        """
        part = self[key]

        # Initialize cached quantity if this is a new part entry
        if part["qty"] == 0:
            if " | " in key:
                cat_str, val_str = key.split(" | ", 1)
                try:
                    from src.bom_lib.classifier import normalize_value_to_quantity

                    cat = ComponentCategory(cat_str)
                    part["val_qty"] = normalize_value_to_quantity(cat, val_str)
                except ValueError:
                    part["val_qty"] = None
            else:
                part["val_qty"] = None

        part["qty"] += qty

        if ref:
            part["refs"].append(ref)
            part["sources"][source].append(ref)

    def merge(self, other: Inventory, multiplier: int = 1) -> None:
        """Merges another inventory into this one.

        Args:
            other: The Inventory object to merge in.
            multiplier: Multiplication factor for the incoming inventory quantities.
        """
        for key, data in other.items():
            self[key]["qty"] += data["qty"] * multiplier
            self[key]["refs"].extend(data["refs"])
            for src, refs in data["sources"].items():
                self[key]["sources"][src].extend(refs * multiplier)


def create_empty_stats() -> StatsDict:
    """Factory function to return a zeroed StatsDict."""
    return {
        "lines_read": 0,
        "parts_found": 0,
        "residuals": [],
        "extracted_title": None,
        "seen_refs": set(),
        "errors": [],
    }


def create_empty_inventory() -> Inventory:
    """Factory function to return new Inventory instance."""
    return Inventory()
