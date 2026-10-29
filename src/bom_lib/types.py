"""Type definitions and shared data structures for the BOM library.

This module contains the TypedDicts and type aliases used throughout the
parsing and sourcing pipeline to ensure consistent data passing.
"""

import copy
import hashlib
import re
from collections import UserDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import (
    Any,
    NamedTuple,
    NotRequired,
    Protocol,
    TypedDict,
    cast,
    runtime_checkable,
)

import pint

from src.bom_lib.enums import (
    ComponentCategory,
    ComponentOrigin,
    ComponentSpec,
    HardwareRole,
    OcrVerdict,
    ParseWarningKind,
    PcbShop,
)
from src.bom_lib.errors import ReadError


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


# Characters that make a spreadsheet read a cell as a formula (OWASP CSV
# injection). Exporters prefix such text with an apostrophe.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

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


@dataclass(frozen=True, slots=True)
class FileUpload:
    """A file the builder gave, whatever the frontend received it as.

    Frontends hand uploads to the backend in this form, so no strategy ever
    sees a UI framework's file object.

    Attributes:
        name: The file's name, whose extension picks the parser.
        content: The file's bytes.
    """

    name: str
    content: bytes

    def getvalue(self) -> bytes:
        """The file's bytes, as a file buffer exposes them."""
        return self.content

    @property
    def fingerprint(self) -> str:
        """Identifies the file by name and content, to tell when it changed."""
        return f"{self.name}:{hashlib.sha256(self.content).hexdigest()[:16]}"


@dataclass(frozen=True, slots=True)
class BuildHardware:
    """What one project needs from the app's standard hardware kit.

    Attributes:
        count: Number of pedals of this project being built.
        provided: Hardware the project's own parts list already includes.
        status_leds: Status LEDs per pedal, each with its own resistor
            (GuitarPCB Space Modulator lights EFL1-3).
    """

    count: int
    provided: frozenset[HardwareRole] = frozenset()
    status_leds: int = 1


@dataclass(frozen=True, slots=True)
class Location:
    """Where a part was read: a box on one page of its document.

    Coordinates are in points from the page's top-left corner, as pdfplumber
    and PDF viewers measure them.

    Attributes:
        page: The page (0-based).
        x0: Left edge.
        top: Top edge.
        x1: Right edge.
        bottom: Bottom edge.
    """

    page: int
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True, slots=True)
class ReadPart:
    """One designator a project's parts list read, and where it was printed.

    Attributes:
        ref: The designator ('R1'; 'R1 (2)' for the second board's R1).
        category: What kind of part it is.
        value: Its value, as the parts list holds it ('10k').
        location: Where in the document it was read; None when it wasn't
            found printed, or the parts list isn't a document.
    """

    ref: str
    category: ComponentCategory
    value: str
    location: Location | None = None


@dataclass(frozen=True, slots=True)
class BuiltProject:
    """One project as it went onto the master list.

    Attributes:
        name: The project's name: as typed, else the document's title or
            file name, else its place in the list ('Project #2').
        label: The name with the chosen build ('Muffin Fuzz (Civil War)'):
            how the parts' sources and every output name the project.
        hardware: What it needs from the standard hardware kit.
        warnings: Its parser self-check failures, to review.
        errors: Why its parts list couldn't be read, each with its bug if
            it was one in Star Ground.
        options: Interchangeable choices resolved to the document's default.
        document: The source PDF's bytes, for the build pack.
        source_path: A preset's typed parts list on disk, for the build pack.
        source_url: A preset's build document at its publisher, linked from
            the build pack.
        pcb_shop: Where its board is bought, when a shop sells it.
        parts: Each designator its parts list read, in the order read, for
            checking against the document.
    """

    name: str
    label: str
    hardware: BuildHardware
    warnings: tuple[ParseWarning, ...] = ()
    errors: tuple[ReadError, ...] = ()
    options: tuple[str, ...] = ()
    document: bytes | None = None
    source_path: str | None = None
    source_url: str | None = None
    pcb_shop: PcbShop | None = None
    parts: tuple[ReadPart, ...] = ()


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """A parser self-check failure that the user must review.

    Warnings mark places where the output may silently disagree with the
    source document, e.g. a designator printed in the PDF that never made it
    into the inventory.

    Attributes:
        kind: The self-check that failed.
        ref: The designator concerned.
        message: Human-readable explanation.
    """

    kind: ParseWarningKind
    ref: str
    message: str

    def __str__(self) -> str:
        """Returns the warning formatted for display."""
        return f"{self.kind.value}: {self.message}"


class StatsDict(TypedDict):
    """Tracking metrics and errors for a single ingestion session.

    Attributes:
        lines_read: Total lines processed from the source file.
        parts_found: Number of valid component matches extracted.
        residuals: Lines that were rejected/skipped during parsing.
        extracted_title: Heuristic guess at the project title (PDF only).
        seen_refs: Raw value recorded for each reference seen so far, used to
            prevent duplicate counting and to detect conflicting duplicates.
        unread_counted: Rows counting parts by value ('3 - jumpers') that sat
            in a parts list but could not be classified.
        errors: Fatal problems that aborted all or part of the parse, for
            the builder; a bug in Star Ground carries its `Bug`.
        warnings: Self-check failures where the output may be incomplete.
    """

    lines_read: int
    parts_found: int
    residuals: list[str]
    extracted_title: str | None
    seen_refs: dict[str, str]
    unread_counted: list[str]
    errors: list[ReadError]
    warnings: list[ParseWarning]


class PartData(TypedDict):
    """Structure representing a specific component's aggregate data.

    Attributes:
        qty: Total quantity required across all projects.
        val_qty: The cached physical quantity or Decimal value of the component
                 (e.g. 10000 * ureg.ohm for '10k' Resistors).
                 None if the value is non-numeric (e.g. 'TL072').
        refs: List of designators (e.g., ['R1', 'R2']).
        sources: Mapping of project names to the specific refs they contributed.
        included: How many of `qty` come with a PCB ('IC3  24LC32A *Included',
            PedalPCB Deflector): listed, but not bought.
        counted: How many one build of each source counts by value, with no
            designator ('4 - 10k', Tonepad). Like a source's designators, it
            describes one pedal, whatever the number built.
    """

    qty: int
    val_qty: pint.Quantity[Any] | Decimal | None
    refs: list[str]
    sources: dict[str, list[str]]
    included: int
    counted: dict[str, int]


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


class PDFWord(TypedDict):
    """A positioned word extracted from a PDF page (pdfplumber word dict subset).

    Attributes:
        text: The word text.
        x0: Left edge, in points.
        x1: Right edge, in points.
        top: Top edge, in points from the top of the page.
        size: Font size, in points.
    """

    text: str
    x0: float
    x1: float
    top: float
    size: float


@dataclass(frozen=True)
class Picture:
    """An image drawn on a PDF page, in points from the page's top-left corner."""

    x0: float
    top: float
    x1: float
    bottom: float


class PDFPageExtraction(TypedDict):
    """Extracted text and tabular layout from a single PDF page.

    Attributes:
        tables: List of extracted tables (each table is rows of string cells or None).
        text: Raw extracted text string from the page.
        words: Positioned words, used for column-aware layout analysis.
        labels: Words printed in the margin beside a table, which name parts
            of it ('Standard RAT' beside one build's rows); OCR keeps them
            apart from the table's words.
        pictures: The page's large images, where a parts table may be drawn
            as a picture rather than text.
        marked: Rows whose value carries a numbered footnote marker, as
            (row label, marker) pairs (see `layout.marked_labels`).
    """

    tables: list[list[list[str | None]]]
    text: str | None
    words: list[PDFWord]
    labels: NotRequired[list[PDFWord]]
    pictures: NotRequired[list[Picture]]
    marked: NotRequired[list[tuple[str, str]]]


ShoppingListRow = TypedDict(
    "ShoppingListRow",
    {
        "Origin": ComponentOrigin,
        "Category": str,
        "Part": str,
        "BOM Qty": int,
        "Included": int,
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
            "included": 0,
            "counted": {},
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

    def add_counted(self, source: str, key: ComponentKey, qty: int) -> None:
        """Records parts the document counts by value, with no designator.

        Args:
            source: Source identifier (e.g., "Big Muff").
            key: The unique component key.
            qty: How many to buy.
        """
        self.add_part(source, key, "", qty)
        part = self[key]
        part["counted"][source] = part["counted"].get(source, 0) + qty
        part["sources"].setdefault(source, [])

    def merge(self, other: Inventory, multiplier: int = 1) -> None:
        """Merges another inventory into this one.

        Args:
            other: The Inventory object to merge in.
            multiplier: Multiplication factor for the incoming inventory quantities.
        """
        for key, data in other.items():
            self[key]["qty"] += data["qty"] * multiplier
            self[key]["included"] += data["included"] * multiplier
            for src, n in data["counted"].items():
                counted = self[key]["counted"]
                counted[src] = max(counted.get(src, 0), n)
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
        "seen_refs": {},
        "unread_counted": [],
        "errors": [],
        "warnings": [],
    }


def create_empty_inventory() -> Inventory:
    """Factory function to return new Inventory instance."""
    return Inventory()


@dataclass(frozen=True, slots=True)
class BuildVariant:
    """One circuit described by a multi-circuit document (e.g. a Muffin version).

    Attributes:
        name: The variant name as printed (e.g. 'Civil War', 'Germanium Version').
        inventory: The complete parts list for this build.
        parts_found: Number of parts in the build.
        locations: Where each of its designators was read: builds can list
            the same designator in different rows.
    """

    name: str
    inventory: Inventory
    parts_found: int
    locations: Mapping[str, Location] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptionNote:
    """An interchangeable choice the parser resolved to the document's default.

    Options do not change the circuit (through-hole or SMD transistors, a bridge
    rectifier or four diodes), so the first choice is used and the builder is
    told about the alternative.

    Attributes:
        group: What the choice is about (e.g. 'SW1', 'Transistors').
        chosen: The choice used (e.g. 'Through-Hole').
        alternatives: The other choices, as printed.
    """

    group: str
    chosen: str
    alternatives: tuple[str, ...]

    def __str__(self) -> str:
        """Returns the note formatted for display."""
        return f"{self.group}: using {self.chosen}; alternatively {' or '.join(self.alternatives)}"


@dataclass(frozen=True, slots=True)
class OcrAttempt:
    """Parts read by OCR from a document's image pages, not yet accepted.

    Attributes:
        verdict: How far the reading can be trusted.
        inventory: The parts OCR found that the text layer did not; empty when
            the reading holds several builds.
        warnings: One 'Read by OCR' warning per part.
        pages: The pages read (0-based).
        parts_read: How many designators OCR found.
        suspect: How many of them have implausible values.
        supplements: Whether the text layer listed some parts already (so OCR
            only fills in the rest).
        variants: The builds OCR read, when the parts list describes several
            (version columns, a second build's table); empty otherwise.
        locations: Where each designator in `inventory` was found on its page
            (each build's are its own).
    """

    verdict: OcrVerdict
    inventory: Inventory
    warnings: tuple[ParseWarning, ...]
    pages: tuple[int, ...]
    parts_read: int
    suspect: int
    supplements: bool
    variants: tuple[BuildVariant, ...] = ()
    locations: Mapping[str, Location] = field(default_factory=dict)

    @property
    def has_parts(self) -> bool:
        """Whether there is anything to accept."""
        return bool(self.inventory) or bool(self.variants)

    @property
    def message(self) -> str:
        """Plain-language explanation of the attempt, for the builder."""
        where = (
            "Some of this document's parts appear to be printed as an image"
            if self.supplements
            else "This document's parts list appears to be printed as an image"
        )
        if self.verdict == OcrVerdict.UNUSABLE:
            # Bonus PCBs print values on the board and have no parts list at
            # all: the document may simply have none.
            return (
                "No parts list could be read from this document, even with OCR "
                "(text recognition). If it has one, it is likely an image OCR "
                "could not read: enter the parts by hand."
            )
        found = f"OCR (text recognition) read {self.parts_read} parts from it"
        if self.variants:
            found += f", in {len(self.variants)} builds"
        if self.verdict == OcrVerdict.PARTIAL:
            verb = "looks" if self.suspect == 1 else "look"
            wrong = f", and {self.suspect} {verb} wrong" if self.suspect else ""
            return (
                f"{where}. {found}{wrong}, but the reading looks incomplete, so "
                "OCR is unlikely to be reliable here. You can add these parts, "
                "but check every one against the document and enter any missing "
                "parts by hand."
            )
        return (
            f"{where}. {found}. OCR is less reliable than reading text: check "
            "each part against the document before buying."
        )


@dataclass(frozen=True, slots=True)
class ParseResult:
    """The structured result of parsing a BOM source.

    A document that describes several circuits yields ``variants`` and an
    *empty* ``inventory``: nothing may be bought until a build is chosen with
    `select`, so no variant is ever used by default.

    Attributes:
        inventory: Populated Inventory mapping component keys to PartData.
        stats: Parsing statistics, errors, and metadata.
        title: Optional extracted project or document title.
        raw_content: Optional raw document bytes or content representation.
        variants: Every build of a multi-circuit document; empty otherwise.
        options: Interchangeable choices resolved to the document's default.
        ocr: Parts OCR read from image pages, offered to the builder; they are
            not in ``inventory`` until `accept_ocr`.
        locations: Where in the document each designator in `inventory` was
            read (see `locations.locate`; each build's are its own). A part
            read without a designator, or one not found printed, has none.
    """

    inventory: Inventory
    stats: StatsDict
    title: str | None = None
    raw_content: bytes | None = None
    variants: tuple[BuildVariant, ...] = ()
    options: tuple[OptionNote, ...] = ()
    ocr: OcrAttempt | None = None
    locations: Mapping[str, Location] = field(default_factory=dict)

    @property
    def requires_choice(self) -> bool:
        """Whether a build must be chosen before the parts list can be used."""
        return bool(self.variants)

    @property
    def variant_names(self) -> tuple[str, ...]:
        """The names of the available builds, in document order."""
        return tuple(v.name for v in self.variants)

    def select(self, name: str) -> ParseResult:
        """Returns the result for one build of a multi-circuit document.

        Args:
            name: One of `variant_names`.

        Returns:
            A single-build ParseResult with that variant's parts list.

        Raises:
            KeyError: If the document has no variant with that name.
        """
        variant = next((v for v in self.variants if v.name == name), None)
        if variant is None:
            raise KeyError(
                f"No build named {name!r}; choose one of {self.variant_names}"
            )
        stats = cast(StatsDict, {**self.stats, "parts_found": variant.parts_found})
        return replace(
            self,
            inventory=variant.inventory,
            stats=stats,
            variants=(),
            locations=variant.locations,
        )

    def accept_ocr(self) -> ParseResult:
        """Returns the result with the parts OCR read added to the parts list.

        Each added part carries a 'Read by OCR' warning, and the warnings OCR
        answered (no parts list, designators missing from it) are dropped.

        Returns:
            The result with the OCR parts merged and no pending OCR attempt.

        Raises:
            ValueError: If there are no OCR parts to accept.
        """
        if self.ocr is None or not self.ocr.has_parts:
            raise ValueError("No OCR parts to accept")
        if self.ocr.variants:
            # The builds OCR read become the document's builds, each with the
            # parts the text layer read too.
            variants = tuple(
                replace(
                    v,
                    inventory=self._with(v.inventory),
                    parts_found=v.parts_found + self.stats["parts_found"],
                    locations={**self.locations, **v.locations},
                )
                for v in self.ocr.variants
            )
            return replace(
                self,
                inventory=Inventory(),
                stats=self._ocr_stats(self.ocr),
                variants=variants,
                ocr=None,
                locations={},
            )
        inventory = self._with(self.ocr.inventory)
        stats = self._ocr_stats(self.ocr)
        stats["parts_found"] += sum(d["qty"] for d in self.ocr.inventory.values())
        return replace(
            self,
            inventory=inventory,
            stats=stats,
            ocr=None,
            locations={**self.locations, **self.ocr.locations},
        )

    def _with(self, parts: Inventory) -> Inventory:
        """This result's parts list with other parts added."""
        inventory = copy.deepcopy(self.inventory)
        for key, data in parts.items():
            if key in inventory:
                inventory.merge(Inventory({key: data}))
            else:
                inventory[key] = copy.deepcopy(data)
        return inventory

    def _ocr_stats(self, ocr: OcrAttempt) -> StatsDict:
        """The stats with OCR's warnings, less those its reading answered."""
        inventories = [ocr.inventory, *(v.inventory for v in ocr.variants)]
        read = {r for inv in inventories for data in inv.values() for r in data["refs"]}
        warnings = [
            w
            for w in self.stats["warnings"]
            if w.kind != ParseWarningKind.NO_PARTS_LIST
            and not (w.kind == ParseWarningKind.UNEXTRACTED_REF and w.ref in read)
        ]
        return cast(
            StatsDict,
            {**self.stats, "warnings": warnings + list(ocr.warnings)},
        )
