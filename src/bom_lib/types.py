"""Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

import uuid
from collections import UserDict, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple, NotRequired, Protocol, TypedDict, runtime_checkable

import pint

from src.bom_lib.enums import (
    ComponentCategory,
    ComponentOrigin,
    ComponentSpec,
    InputMethod,
)


@runtime_checkable
class SupportsRead(Protocol):
    """Protocol for file-like objects providing a read method."""

    def read(self, *args: Any, **kwargs: Any) -> Any:
        """Read and return content from the underlying stream."""
        ...


@runtime_checkable
class SupportsGetValue(Protocol):
    """Protocol for buffer objects providing a getvalue method (e.g. UploadedFile, BytesIO)."""

    def getvalue(self) -> Any:
        """Return the complete buffer contents as bytes or str."""
        ...


RawBOMData = (
    str | bytes | bytearray | list[str] | SupportsRead | SupportsGetValue | None
)


@dataclass
class ProjectSlot:
    """Represents the UI state for a single pedal project slot.

    Shared between the Streamlit frontend and PDF generation backend.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    method: InputMethod = InputMethod.PASTE_TEXT
    count: int = 1
    data: RawBOMData = None

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


@dataclass(frozen=True, slots=True)
class ComponentKey:
    """Strongly-typed identity for an electronic component in inventory.

    Attributes:
        category: The standardized ComponentCategory enum.
        value: The normalized component value or description string.
    """

    category: ComponentCategory
    value: str

    def __str__(self) -> str:
        """Returns formatted string representation formatted as 'Category | Value'."""
        return f"{self.category.value} | {self.value}"

    def __lt__(self, other: object) -> bool:
        """Orders component keys by category value followed by part value."""
        if not isinstance(other, ComponentKey):
            return NotImplemented
        return (self.category.value, self.value) < (
            other.category.value,
            other.value,
        )

    @classmethod
    def from_string(cls, raw: str) -> ComponentKey:
        """Parses a formatted string key (e.g., 'Resistors | 10k') into a ComponentKey.

        Args:
            raw: Formatted string key.

        Returns:
            A strongly-typed ComponentKey instance.
        """
        if " | " not in raw:
            return cls(ComponentCategory.UNKNOWN, raw)
        cat_str, val = raw.split(" | ", 1)
        try:
            category = ComponentCategory(cat_str)
        except ValueError:
            category = ComponentCategory.UNKNOWN
        return cls(category, val)


@dataclass(frozen=True, slots=True)
class CategorizationResult:
    """Result of classifying a component designator and value.

    Attributes:
        category: The standardized ComponentCategory enum.
        clean_value: The normalized component value string.
        injected_key: Optional secondary component key to inject (e.g. DIP socket).
    """

    category: ComponentCategory
    clean_value: str
    injected_key: ComponentKey | None = None


@dataclass(frozen=True, slots=True)
class PurchaseRecommendation:
    """Recommended purchase quantity and sourcing guidance.

    Attributes:
        buy_qty: Recommended purchase quantity with buffer applied.
        note: Sourcing notes, package warnings, or alternatives.
    """

    buy_qty: int
    note: str


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


class ResolvedPartSourcing(NamedTuple):
    """Resolved purchasing and supplier details for a component.

    Attributes:
        origin: Sourcing origin (Circuit Board, Hardware Kit, Extras).
        buy_qty: Recommended purchase quantity with buffer applied.
        notes: Sourcing notes, package warnings, or Silicon Sommelier recommendations.
        spec_type: Physical material/dielectric specification.
        search_term: Optimized search string for supplier catalog lookup.
        supplier_url: Direct link to supplier product page or catalog search.
    """

    origin: ComponentOrigin
    buy_qty: int
    notes: str
    spec_type: ComponentSpec
    search_term: str
    supplier_url: str


class ChecklistPart(TypedDict):
    """Component checklist item for PDF Field Manual generation.

    Attributes:
        category: Component category enum (e.g., ComponentCategory.RESISTORS).
        value: Cleaned component value string (e.g., '10k', 'TL072').
        qty: Total count of this part in the project.
        refs: List of designators for this component (e.g., ['R1', 'R2']).
        notes: Build annotations (e.g. '[!] Check Size').
        polarized: True if component requires orientation verification.
        spec_type: Optional physical component specification (e.g. ComponentSpec.ELECTROLYTIC).
    """

    category: ComponentCategory
    value: str
    qty: int
    refs: list[str]
    notes: str
    polarized: bool
    spec_type: NotRequired[ComponentSpec]


class PDFPageExtraction(TypedDict):
    """Extracted text and tabular layout from a single PDF page.

    Attributes:
        tables: List of extracted tables (each table is rows of string cells or None).
        text: Raw extracted text string from the page.
    """

    tables: list[list[list[str | None]]]
    text: str | None


ShoppingListRow = TypedDict(
    "ShoppingListRow",
    {
        "Origin": ComponentOrigin,
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


def make_component_key(category: ComponentCategory, val: str) -> ComponentKey:
    """Creates a standardized component key for inventory dictionaries.

    Args:
        category: Component category enum.
        val: Component value or part name string.

    Returns:
        Standardized ComponentKey instance.
    """
    return ComponentKey(category=category, value=val)


def parse_component_key(
    key: ComponentKey | str,
) -> tuple[ComponentCategory, str]:
    """Parses a component key or string into (ComponentCategory, value).

    If the key does not contain the ' | ' delimiter or has an unrecognized
    category, returns (ComponentCategory.UNKNOWN, key).

    Args:
        key: A ComponentKey instance or standardized inventory key string.

    Returns:
        Tuple of (ComponentCategory, value_string).
    """
    if isinstance(key, ComponentKey):
        return key.category, key.value
    k = ComponentKey.from_string(key)
    return k.category, k.value


class Inventory(UserDict[ComponentKey, PartData]):
    """Stateful domain model for tracking aggregated BOM components.

    Encapsulates storage, mutation, and aggregation logic to prevent
    invalid state transitions (e.g., assigning string to quantity).
    """

    def __init__(self, data: dict[ComponentKey, PartData] | None = None) -> None:
        super().__init__(data)
        # Ensure default factory behavior for new keys
        if self.data is None:
            self.data = {}

    @staticmethod
    def _coerce_key(key: ComponentKey | str) -> ComponentKey:
        if isinstance(key, ComponentKey):
            return key
        return ComponentKey.from_string(key)

    def __getitem__(self, key: ComponentKey | str) -> PartData:
        """Retrieves part data by ComponentKey or coerced string key."""
        return super().__getitem__(self._coerce_key(key))

    def __setitem__(self, key: ComponentKey | str, item: PartData) -> None:
        """Assigns part data by ComponentKey or coerced string key."""
        super().__setitem__(self._coerce_key(key), item)

    def __delitem__(self, key: ComponentKey | str) -> None:
        """Deletes part data by ComponentKey or coerced string key."""
        super().__delitem__(self._coerce_key(key))

    def __contains__(self, key: object) -> bool:
        """Checks whether a ComponentKey or string key exists in inventory."""
        if isinstance(key, (ComponentKey, str)):
            return super().__contains__(self._coerce_key(key))
        return False

    def get(self, key: ComponentKey | str, default: Any = None) -> Any:
        """Gets part data by ComponentKey or coerced string key with fallback."""
        return super().get(self._coerce_key(key), default)

    def __missing__(self, key: ComponentKey) -> PartData:
        """Default factory for new parts."""
        value: PartData = {
            "qty": 0,
            "val_qty": None,
            "refs": [],
            "sources": defaultdict(list),
        }
        self.data[key] = value
        return value

    def add_part(
        self, source: str, key: ComponentKey | str, ref: str, qty: int = 1
    ) -> None:
        """Records a part in the inventory.

        Args:
            source: Source identifier (e.g., "Big Muff").
            key: The unique component key or formatted key string.
            ref: The reference designator (e.g., "R1").
            qty: Quantity to add.
        """
        k = self._coerce_key(key)
        part = self[k]

        # Initialize cached quantity if this is a new part entry
        if part["qty"] == 0:
            if k.category != ComponentCategory.UNKNOWN:
                try:
                    from src.bom_lib.classifier import normalize_value_to_quantity

                    part["val_qty"] = normalize_value_to_quantity(k.category, k.value)
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
            k = self._coerce_key(key)
            self[k]["qty"] += data["qty"] * multiplier
            self[k]["refs"].extend(data["refs"])
            for src, refs in data["sources"].items():
                self[k]["sources"][src].extend(refs * multiplier)


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


@dataclass(frozen=True, slots=True)
class ParseResult:
    """The structured result of parsing a BOM source.

    Attributes:
        inventory: Populated Inventory mapping component keys to PartData.
        stats: Parsing statistics, errors, and metadata.
        title: Optional extracted project or document title.
        raw_content: Optional raw document bytes or content representation.
    """

    inventory: Inventory
    stats: StatsDict
    title: str | None = None
    raw_content: bytes | None = None
