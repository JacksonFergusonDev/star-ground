"""The HTTP API's request and response bodies.

These are the API's contract with the frontend: the OpenAPI schema generated
from them (`openapi.json`) is what the frontend's TypeScript types come from.
They translate to and from the backend's own types (`src.api`, `src.bom_lib`)
and never expose those types' internals, such as `pint` quantities or file
bytes.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.bom_lib import ComponentCategory, ComponentOrigin, OcrVerdict, ParseWarningKind
from src.bom_lib.errors import ErrorKind
from src.bom_lib.stages import BuildStage

# Generous for any real parts list, small enough to bound a request's work.
MAX_TEXT_CHARS = 200_000
MAX_PROJECTS = 50
MAX_COUNT = 100
# More than any parts list has designators.
MAX_CORRECTIONS = 500
# `src.bom_lib.errors.record_bug`'s codes: six hex digits.
BUG_CODE = r"^[0-9A-F]{6}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartCorrection(_Model):
    """A part the builder corrected against its document."""

    ref: str = Field(max_length=100, description="The designator, as read.")
    category: ComponentCategory
    value: str = Field(max_length=200, description="As `POST /api/parts/read` read it.")


class _ProjectBase(_Model):
    name: str = Field(
        "", max_length=200, description="Blank to use the document's title."
    )
    count: int = Field(1, ge=1, le=MAX_COUNT, description="Number of pedals.")
    variant: str | None = Field(
        None, description="The build chosen from a multi-circuit document."
    )
    use_ocr: bool = Field(False, description="Add the parts OCR read from images.")
    corrections: list[PartCorrection] = Field(
        default_factory=list,
        max_length=MAX_CORRECTIONS,
        description="Parts the builder corrected, applied to the chosen build.",
    )


class PasteProject(_ProjectBase):
    """A parts list typed or pasted as `REF VALUE` lines."""

    method: Literal["paste"]
    text: str = Field(max_length=MAX_TEXT_CHARS)


class PresetProject(_ProjectBase):
    """A preset from the library, by its key (see `GET /api/presets`)."""

    method: Literal["preset"]
    preset: str


class UrlProject(_ProjectBase):
    """A build document at a public link."""

    method: Literal["url"]
    url: str = Field(max_length=2048)


class UploadProject(_ProjectBase):
    """An uploaded build document.

    It's a file part of the same request, or a reading's `document`, so a page
    needn't send the file with every change.
    """

    method: Literal["upload"]
    file: str | None = Field(None, description="The file part's filename.")
    document: str | None = Field(
        None,
        description="A reading's `document`, instead of the file. If this server "
        "no longer holds it, the request fails with `DocumentsMissing`; send the "
        "file instead.",
    )

    @model_validator(mode="after")
    def _names_the_document(self) -> UploadProject:
        if self.file is None and self.document is None:
            raise ValueError("Send the upload as a `file` or a `document`.")
        return self


ProjectIn = Annotated[
    PasteProject | PresetProject | UrlProject | UploadProject,
    Field(discriminator="method"),
]


class MasterListRequest(_Model):
    """The projects to merge, and how to list what to buy."""

    projects: list[ProjectIn] = Field(min_length=1, max_length=MAX_PROJECTS)
    show_hardware: bool = Field(True, description="List the hardware kit.")
    show_extras: bool = Field(True, description="List sockets and adapters.")


class LinkFormat(StrEnum):
    """How the shopping list CSV writes supplier links."""

    PLAIN = "plain"
    EXCEL = "excel"


class ExportKind(StrEnum):
    """The files a master list exports."""

    SHOPPING_LIST = "shopping-list"
    SUPPLIER_CART = "supplier-cart"
    STOCK_UPDATE = "stock-update"
    FIELD_MANUALS = "field-manuals"
    BUILD_PACK = "build-pack"


class PartWarning(_Model):
    """A place where the parts list may disagree with its document."""

    kind: ParseWarningKind
    ref: str
    message: str


class OcrReading(_Model):
    """What OCR read from a document's image pages, offered to the builder."""

    verdict: OcrVerdict
    message: str
    parts_read: int
    suspect: int
    pages: list[int]
    has_parts: bool


class Problem(_Model):
    """Why a request or a reading failed, written for the builder."""

    kind: ErrorKind | None = Field(
        default=None,
        description="What kind of problem it is; none for a plain refusal.",
    )
    message: str = Field(description="What happened.")
    hint: str = Field(default="", description="What the builder can do about it.")
    bug_code: str | None = Field(
        default=None,
        description="Set when it's a bug in Star Ground: names it in the server's "
        "log, and a report sends it back.",
    )


class DocumentReading(_Model):
    """An upload or link, read ahead of building the master list."""

    document: str | None = Field(
        description="Names an upload in later master list requests, in place of "
        "the file; none for links, whose URL names them."
    )
    title: str | None = Field(description="The title the document prints.")
    file_title: str | None = Field(description="The file's name, as a fallback.")
    parts_found: int
    builds: list[str] = Field(
        description="The builds to choose from; empty when there's only one."
    )
    ocr_builds: list[str] = Field(
        description="The builds to choose from if OCR's parts are accepted."
    )
    ocr: OcrReading | None = Field(
        description="What OCR read, when the document was read with `ocr`."
    )
    ocr_offered: bool = Field(
        description="OCR could add parts this reading lacks (its parts list is an "
        "image, or names parts it doesn't list): read it again with `ocr` once "
        "the builder accepts."
    )
    options: list[str] = Field(description="Interchangeable parts, resolved.")
    errors: list[Problem] = Field(description="Why it couldn't be read.")


class ReadQueued(_Model):
    """The read is waiting for a free reader; OCR is limited per server."""

    event: Literal["queued"] = "queued"
    position: int


class ReadStarted(_Model):
    """The document is being read."""

    event: Literal["reading"] = "reading"


class ReadDone(_Model):
    """The document's reading."""

    event: Literal["result"] = "result"
    reading: DocumentReading


class ReadFailed(Problem):
    """The document couldn't be read."""

    event: Literal["error"] = "error"


ReadEvent = Annotated[
    ReadQueued | ReadStarted | ReadDone | ReadFailed, Field(discriminator="event")
]


class PartReview(_Model):
    """One part the self-checks flagged, with every check it failed."""

    ref: str
    warnings: list[PartWarning]


class SourceLocation(_Model):
    """A box on one page of a document, in points from the page's top-left."""

    page: int = Field(description="The page, counting from 0.")
    x0: float
    top: float
    x1: float
    bottom: float


class ReadPartOut(_Model):
    """One designator a project's parts list read, and where it was printed."""

    ref: str
    category: ComponentCategory
    value: str
    location: SourceLocation | None = Field(
        description="Where in the document it was read; none when it wasn't "
        "found printed, or the parts list was pasted."
    )


class BuildStep(_Model):
    """One stage of soldering a project, lowest parts first."""

    stage: BuildStage
    refs: list[str] = Field(description="Its designators, in the order read.")


class ProjectResult(_Model):
    """One project as it went onto the master list."""

    label: str = Field(description="The name with its build: 'Muffin Fuzz (Ram)'.")
    name: str
    reviews: list[PartReview]
    ocr_reads: list[PartWarning] = Field(description="Parts read by OCR, to check.")
    options: list[str]
    errors: list[Problem] = Field(description="Why it couldn't be read.")
    parts: list[ReadPartOut] = Field(
        description="Each designator read, in the order read, to check against "
        "the document."
    )
    build_order: list[BuildStep] = Field(
        description="Its designators by the stage they're soldered in, lowest "
        "parts first, as the field manual orders them."
    )


class ShoppingRow(_Model):
    """One part to buy."""

    origin: ComponentOrigin
    category: str
    part: str
    bom_qty: int
    included: int
    in_stock: int | None
    net_need: int | None
    buy_qty: int
    notes: str
    search_term: str
    supplier_url: str


class Stats(_Model):
    """Totals across projects."""

    lines_read: int
    parts_found: int


class MasterListResult(_Model):
    """Every project's parts, merged, with what to buy."""

    projects: list[ProjectResult]
    shopping_list: list[ShoppingRow]
    stats: Stats
    ignored_lines: list[str] = Field(
        description="Skipped lines that looked like parts."
    )
    has_stock: bool


class BuildNotChosen(Problem):
    """A multi-circuit document's build wasn't chosen."""

    projects: list[str]


class DocumentsMissing(Problem):
    """Uploads named by `document` that this server no longer holds."""

    documents: list[str] = Field(description="Send these as files instead.")


class Preset(_Model):
    """A circuit in the preset library."""

    key: str
    source: str
    category: str
    name: str


class PresetCatalog(_Model):
    """The preset library."""

    sources: list[str]
    categories: dict[str, list[str]]
    presets: list[Preset]


class FeedbackIn(_Model):
    """A rating and comment from the app."""

    rating: int = Field(ge=1, le=5, description="1 (terrible) to 5 (excellent).")
    comment: str = Field("", max_length=5000)


class ReportIn(_Model):
    """A builder's report of a bug, sent from where it appeared."""

    code: str = Field(pattern=BUG_CODE, description="The bug's `bug_code`.")
    document: str = Field(
        max_length=2048,
        description="What was being read: the upload's name or the link. Never "
        "the document itself.",
    )
    comment: str = Field("", max_length=5000, description="Anything the builder adds.")


class PartIn(_Model):
    """A value the builder typed for a part, to read as the parser would."""

    ref: str = Field(max_length=100)
    value: str = Field(max_length=200)
    category: ComponentCategory | None = Field(
        None, description="The part's category as read, for a named part."
    )


class PartOut(_Model):
    """A part as the master list lists it."""

    category: ComponentCategory
    value: str


class PartReportIn(_Model):
    """A builder's report that the reading got a part wrong.

    It feeds the parser's regression corpus: the document's link names it,
    and the location says where to look.
    """

    document: str = Field(
        max_length=2048,
        description="The link, or an upload's name and `document`. Never the "
        "document itself.",
    )
    ref: str = Field(max_length=100)
    read: str = Field(max_length=200, description="The value as Star Ground read it.")
    correction: str | None = Field(
        None, max_length=200, description="The value the document prints."
    )
    location: SourceLocation | None = None
    comment: str = Field("", max_length=5000, description="Anything the builder adds.")


class Health(_Model):
    """The service is up."""

    status: Literal["ok"] = "ok"
