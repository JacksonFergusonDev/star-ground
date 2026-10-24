"""The backend's API: what a frontend calls to turn projects into a master list.

A frontend gathers each project's inputs into a `Project`, may read uploads
and links ahead of time with `read_document` (to ask which build, or whether
to use OCR's parts), and calls `generate_master_list`. The `MasterList` it
returns gives the shopping list and the generated documents; the CSVs come
from `src.exporters`.

Nothing here, or in anything it imports, depends on a UI framework: the
frontend owns its widgets and session state, the backend owns every decision
about parts.
"""

import copy
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from src.bom_lib import (
    BOMParserContext,
    BuildHardware,
    BuiltProject,
    FileUpload,
    InputMethod,
    Inventory,
    ParseResult,
    ParseWarning,
    PcbShop,
    ShoppingListRow,
    StatsDict,
    build_shopping_list,
    create_empty_inventory,
    create_empty_stats,
    get_standard_hardware,
    parse_user_inventory,
    provided_hardware,
    rename_source_in_inventory,
    status_leds,
)
from src.pdf_generator import generate_master_zip, generate_pdf_bundle

# Source label used while a document is read ahead of generating; replaced by
# the project's final name when the master list is built.
PENDING_SOURCE = "__pending__"

type ProjectData = str | FileUpload | None
"""A project's parts list: pasted or preset text, a URL, or a file."""


@dataclass(frozen=True, slots=True)
class Project:
    """One project for the master list, as the builder set it.

    Attributes:
        name: The project name typed in; blank to use the document's title.
        count: Number of pedals.
        method: How the parts list is given.
        data: Pasted or preset text, a URL, or an uploaded file.
        variant: The build chosen from a multi-circuit document.
        use_ocr: Whether to add the parts OCR read from image pages.
        reading: The document as `read_document` read it, if it was read
            ahead of time; it is read again otherwise.
        source_path: A preset's typed parts list, for the build pack.
        source_url: A preset's build document at its publisher, for the
            build pack's link.
        pcb_shop: The shop that sells a preset's board.
    """

    name: str = ""
    count: int = 1
    method: InputMethod = InputMethod.PASTE_TEXT
    data: ProjectData = None
    variant: str | None = None
    use_ocr: bool = False
    reading: ParseResult | None = None
    source_path: str | None = None
    source_url: str | None = None
    pcb_shop: PcbShop | None = None

    def __post_init__(self) -> None:
        """Refuses a count that would buy no parts, or a negative number."""
        if self.count < 1:
            raise ValueError(f"A project needs at least one pedal, not {self.count}")


class BuildNotChosenError(ValueError):
    """A multi-circuit document's build was not chosen.

    Building the wrong variant buys the wrong parts, so there is no default.

    Attributes:
        projects: The names of the projects whose build must be chosen.
    """

    def __init__(self, projects: list[str]) -> None:
        super().__init__(
            "Choose which build before generating: "
            + ", ".join(projects)
            + ". Each build of these documents needs different parts."
        )
        self.projects = projects


@dataclass(frozen=True, slots=True)
class MasterList:
    """Every project's parts, merged, with what to buy and what to print.

    Attributes:
        parts: The parts the documents list, merged across projects.
        kit: `parts` plus the standard hardware kit: everything to build.
        stats: Totals across projects, with their errors and warnings.
        projects: Each project as it went onto the list, in input order.
        stock: The builder's stock, subtracted from what to buy.
    """

    parts: Inventory
    kit: Inventory
    stats: StatsDict
    projects: tuple[BuiltProject, ...]
    stock: Inventory | None = None

    @property
    def warnings(self) -> dict[str, list[ParseWarning]]:
        """Each project's self-check failures to review, by label."""
        return {p.label: list(p.warnings) for p in self.projects if p.warnings}

    @property
    def option_notes(self) -> dict[str, list[str]]:
        """Each project's interchangeable parts, by label."""
        return {p.label: list(p.options) for p in self.projects if p.options}

    def shopping_list(
        self, show_hardware: bool = True, show_extras: bool = True
    ) -> list[ShoppingListRow]:
        """What to buy, after stock and parts included with the PCB.

        Args:
            show_hardware: Whether to list the hardware kit.
            show_extras: Whether to list sockets and adapters.

        Returns:
            One row per part, in display order.
        """
        return build_shopping_list(
            self.kit,
            self.stock,
            show_hardware,
            show_extras,
            {p.label: p.pcb_shop for p in self.projects if p.pcb_shop},
        )

    def pdf_bundle(self) -> bytes:
        """A ZIP of each project's field manual and sticker sheet."""
        return generate_pdf_bundle(self.kit, self.projects)

    def build_pack(self, shopping_list_csv: bytes, stock_csv: bytes) -> bytes:
        """A ZIP of everything: the CSVs, generated PDFs and source documents.

        Args:
            shopping_list_csv: From `src.exporters.generate_shopping_list_csv`.
            stock_csv: From `src.exporters.generate_stock_update_csv`.

        Returns:
            The ZIP's bytes.
        """
        return generate_master_zip(
            self.kit, self.projects, shopping_list_csv, stock_csv
        )


def document_key(method: InputMethod, data: ProjectData) -> str | None:
    """Identifies an upload or link, to tell when its reading is stale.

    Only uploads and links are read ahead of time: they are the inputs that
    can be multi-circuit or image-only PDFs. Text and presets are read when
    the master list is generated.

    Args:
        method: How the parts list is given.
        data: The project's parts list.

    Returns:
        The document's identity, or None for text and presets.
    """
    if method == InputMethod.UPLOAD_FILE and isinstance(data, FileUpload):
        return f"file:{data.fingerprint}"
    if method == InputMethod.FROM_URL and data:
        return f"url:{str(data).strip()}"
    return None


def read_document(method: InputMethod, data: ProjectData) -> ParseResult | None:
    """Reads an upload or link ahead of generating the master list.

    The reading says whether a build must be chosen (`requires_choice`) and
    whether OCR offers parts from image pages (`ocr`). An image-only document
    is read by OCR, which takes seconds a page.

    Args:
        method: How the parts list is given.
        data: The project's parts list.

    Returns:
        The reading, or None for inputs read on generating (see `document_key`).
    """
    if document_key(method, data) is None:
        return None
    return BOMParserContext().process(method, data, source_name=PENDING_SOURCE)


def offered(reading: ParseResult, use_ocr: bool) -> ParseResult:
    """A reading as the builder has it: with OCR's parts, if accepted.

    OCR can read several builds, so a build is chosen from this result.

    Args:
        reading: The document's reading.
        use_ocr: Whether the builder accepted OCR's parts.

    Returns:
        The reading, with OCR's parts when accepted and available.
    """
    if use_ocr and reading.ocr is not None and reading.ocr.has_parts:
        return reading.accept_ocr()
    return reading


def file_title(method: InputMethod, data: ProjectData) -> str | None:
    """The document's file name, for a project whose document prints no title.

    Five Cats prints its parts list as an image, so most of its documents
    have no title to read: 'FCP-Tone-Monarch-V1-1' beats 'Project #1'.

    Args:
        method: How the parts list is given.
        data: The project's parts list.

    Returns:
        The file name without its extension, or None.
    """
    if method == InputMethod.UPLOAD_FILE and isinstance(data, FileUpload):
        name = data.name
    elif method == InputMethod.FROM_URL and isinstance(data, str) and data:
        name = unquote(urlsplit(data.strip()).path)
    else:
        return None
    return os.path.splitext(os.path.basename(name))[0] or None


def parts_to_review(warnings: Sequence[ParseWarning]) -> dict[str, list[ParseWarning]]:
    """Groups a project's warnings by designator, in the order first warned.

    One part can fail several self-checks (Aion Amethyst's C1 has a footnote
    and a build note): it is one part to review, listed once.

    Args:
        warnings: A project's warnings.

    Returns:
        Each part's warnings, keyed by its designator.
    """
    groups: dict[str, list[ParseWarning]] = {}
    for i, w in enumerate(warnings):
        groups.setdefault(w.ref or f"#{i}", []).append(w)
    return groups


def read_stock(upload: FileUpload) -> Inventory:
    """Reads the builder's stock CSV (columns: Category, Part, Qty).

    Args:
        upload: The stock file.

    Returns:
        The stock, keyed like the master list's parts.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(upload.content)
    try:
        return parse_user_inventory(tmp.name)
    finally:
        os.remove(tmp.name)


def _read(
    project: Project, source: str, context: BOMParserContext
) -> tuple[ParseResult, str]:
    """The project's parts list, with OCR's if accepted.

    Returns:
        The result, and the source name its parts are listed under.
    """
    if project.reading is None:
        result = context.process(project.method, project.data, source_name=source)
        return offered(result, project.use_ocr), source
    # Work on a copy, so the frontend's cached reading stays pristine.
    return copy.deepcopy(offered(project.reading, project.use_ocr)), PENDING_SOURCE


def _identity(project: Project) -> tuple[object, ...]:
    """What makes two projects the same parts list."""
    document = document_key(project.method, project.data) or project.data
    return (project.method, document, project.variant, project.use_ocr)


def _unique_label(
    label: str, identity: tuple[object, ...], labelled: dict[str, tuple[object, ...]]
) -> str:
    """The label, numbered if another parts list already has it ('Fuzz (2)').

    Parts are listed under their project's label, so two different circuits
    named alike would merge into one field manual with two R1s.

    Args:
        label: The project's label.
        identity: The project's parts list (`_identity`).
        labelled: Each label given so far, with its parts list; updated.

    Returns:
        A label no other parts list has.
    """
    candidate, n = label, 1
    while candidate in labelled and labelled[candidate] != identity:
        n += 1
        candidate = f"{label} ({n})"
    labelled[candidate] = identity
    return candidate


def generate_master_list(
    projects: Sequence[Project], stock: Inventory | None = None
) -> MasterList:
    """Reads every project's parts list and merges them into a master list.

    Args:
        projects: The projects, in the order the builder listed them.
        stock: The builder's stock, if given (see `read_stock`).

    Returns:
        The master list.

    Raises:
        BuildNotChosenError: A multi-circuit document's build is not chosen.
    """
    context = BOMParserContext()
    sources = [
        p.name if p.name.strip() else f"Project #{i}" for i, p in enumerate(projects, 1)
    ]
    readings = [_read(p, s, context) for p, s in zip(projects, sources, strict=True)]

    unchosen = [
        s if p.name.strip() else (r.title or s)
        for p, s, (r, _) in zip(projects, sources, readings, strict=True)
        if r.requires_choice and p.variant not in r.variant_names
    ]
    if unchosen:
        raise BuildNotChosenError(unchosen)

    parts = create_empty_inventory()
    stats = create_empty_stats()
    built: list[BuiltProject] = []
    # Each label's parts list: the same list entered twice shares its label
    # (one field manual for both), a different one gets its own.
    labelled: dict[str, tuple[object, ...]] = {}
    for project, source, (result, listed_as) in zip(
        projects, sources, readings, strict=True
    ):
        chose_build = result.requires_choice
        if chose_build:
            result = result.select(str(project.variant))
        inventory, p_stats = result.inventory, result.stats

        name = source
        title = result.title or file_title(project.method, project.data)
        if title and not project.name.strip():
            name = str(title)
        # Name the build in every output: 'Muffin Fuzz (Civil War)'.
        label = f"{name} ({project.variant})" if chose_build else name
        label = _unique_label(label, _identity(project), labelled)
        if label != listed_as:
            rename_source_in_inventory(inventory, listed_as, label)

        parts.merge(inventory, project.count)
        stats["lines_read"] += p_stats["lines_read"]
        stats["parts_found"] += p_stats["parts_found"]
        stats["residuals"].extend(p_stats["residuals"])
        stats["errors"].extend(p_stats["errors"])
        stats["warnings"].extend(p_stats["warnings"])

        built.append(
            BuiltProject(
                name=name,
                label=label,
                # Standard hardware is skipped for pedals whose document
                # lists its own.
                hardware=BuildHardware(
                    project.count,
                    provided_hardware(inventory),
                    status_leds(inventory),
                ),
                warnings=tuple(p_stats["warnings"]),
                options=tuple(str(o) for o in result.options),
                document=result.raw_content,
                source_path=project.source_path,
                source_url=project.source_url,
                pcb_shop=project.pcb_shop,
            )
        )

    kit = copy.deepcopy(parts)
    get_standard_hardware(kit, [b.hardware for b in built])
    return MasterList(parts, kit, stats, tuple(built), stock)
