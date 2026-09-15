"""Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

import re
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


@runtime_checkable
class SupportsName(Protocol):
    """Protocol for objects exposing a name attribute (e.g. UploadedFile, Path, File)."""

    name: str


RawBOMData = (
    str
    | bytes
    | bytearray
    | list[str]
    | SupportsRead
    | SupportsGetValue
    | SupportsName
    | None
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


@dataclass(frozen=True, slots=True)
class RefDesignator:
    """Strongly-typed reference designator (e.g., 'R1', 'C12', 'SW1', 'HW').

    Attributes:
        prefix: Component prefix (e.g., 'R', 'C', 'IC', 'SW', 'HW').
        number: Numerical index, or None if unnumbered.
        suffix: Optional qualifier or note (e.g., 'A', '(Inj)').
    """

    prefix: str
    number: int | None = None
    suffix: str = ""

    def __str__(self) -> str:
        """Returns the canonical string representation of the reference designator."""
        num_part = str(self.number) if self.number is not None else ""
        if self.suffix:
            if self.suffix.startswith("(") or " " in self.suffix:
                return f"{self.prefix}{num_part} {self.suffix}".strip()
            return f"{self.prefix}{num_part}{self.suffix}"
        return f"{self.prefix}{num_part}"

    @property
    def sort_key(self) -> tuple[str, int, str]:
        """Provides consistent alphanumeric ordering tuple (prefix, number, suffix)."""
        return (
            self.prefix.upper(),
            self.number if self.number is not None else -1,
            self.suffix,
        )

    def __lt__(self, other: object) -> bool:
        """Compares reference designators by alphanumeric sort key."""
        if not isinstance(other, RefDesignator):
            return NotImplemented
        return self.sort_key < other.sort_key

    @classmethod
    def from_string(cls, raw: str) -> RefDesignator:
        """Parses a reference designator string into structured components.

        Examples:
            'R1' -> RefDesignator('R', 1, '')
            'C10' -> RefDesignator('C', 10, '')
            'SW10' -> RefDesignator('SW', 10, '')
            'HW' -> RefDesignator('HW', None, '')
            'VOLUME' -> RefDesignator('VOLUME', None, '')
            'U1 (Inj)' -> RefDesignator('U', 1, '(Inj)')
            'Q1A' -> RefDesignator('Q', 1, 'A')

        Args:
            raw: Raw designator string.

        Returns:
            Structured RefDesignator instance.
        """
        cleaned = raw.strip()
        if not cleaned:
            return cls(prefix="")

        # Match pattern: letter prefix, optional digits, optional suffix
        m = re.match(r"^([a-zA-Z]+)(?:(\d+))?(?:\s*(.*))?$", cleaned)
        if m:
            prefix = m.group(1)
            num_str = m.group(2)
            suffix = (m.group(3) or "").strip()
            num = int(num_str) if num_str is not None else None
            return cls(prefix=prefix, number=num, suffix=suffix)

        # Fallback for strings starting with digits or special characters
        return cls(prefix=cleaned, number=None, suffix="")


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

    def __getitem__(self, key: ComponentKey) -> PartData:
        """Retrieves part data by ComponentKey."""
        return super().__getitem__(key)

    def __setitem__(self, key: ComponentKey, item: PartData) -> None:
        """Assigns part data by ComponentKey."""
        super().__setitem__(key, item)

    def __delitem__(self, key: ComponentKey) -> None:
        """Deletes part data by ComponentKey."""
        super().__delitem__(key)

    def __contains__(self, key: object) -> bool:
        """Checks whether a ComponentKey exists in inventory."""
        if isinstance(key, ComponentKey):
            return super().__contains__(key)
        return False

    def get(self, key: ComponentKey, default: Any = None) -> Any:
        """Gets part data by ComponentKey with default fallback."""
        return super().get(key, default)

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

    def add_part(self, source: str, key: ComponentKey, ref: str, qty: int = 1) -> None:
        """Records a part in the inventory.

        Args:
            source: Source identifier (e.g., "Big Muff").
            key: The unique component key.
            ref: The reference designator (e.g., "R1").
            qty: Quantity to add.
        """
        part = self[key]

        # Initialize cached quantity if this is a new part entry
        if part["qty"] == 0:
            if key.category != ComponentCategory.UNKNOWN:
                try:
                    from src.bom_lib.classifier import normalize_value_to_quantity

                    part["val_qty"] = normalize_value_to_quantity(
                        key.category, key.value
                    )
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
