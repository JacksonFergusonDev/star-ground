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

from src.bom_lib import ComponentOrigin, OcrVerdict, ParseWarningKind

# Generous for any real parts list, small enough to bound a request's work.
MAX_TEXT_CHARS = 200_000
MAX_PROJECTS = 50
MAX_COUNT = 100


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ProjectBase(_Model):
    name: str = Field(
        "", max_length=200, description="Blank to use the document's title."
    )
    count: int = Field(1, ge=1, le=MAX_COUNT, description="Number of pedals.")
    variant: str | None = Field(
        None, description="The build chosen from a multi-circuit document."
    )
    use_ocr: bool = Field(False, description="Add the parts OCR read from images.")


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


class BugReport(_Model):
    """A failure in Star Ground's own code, for the builder to report."""

    code: str = Field(description="Names this occurrence in the server's log.")
    detail: str = Field(
        description="Star Ground's version, what it was doing, and the traceback: "
        "for the report, not to show the builder."
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
    errors: list[str] = Field(description="Why it couldn't be read, in plain words.")
    bugs: list[BugReport] = Field(description="Bugs in Star Ground, to report.")


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


class ReadFailed(_Model):
    """The document couldn't be read."""

    event: Literal["error"] = "error"
    message: str
    bug: BugReport | None = Field(
        None, description="Set when the failure is a bug in Star Ground."
    )


ReadEvent = Annotated[
    ReadQueued | ReadStarted | ReadDone | ReadFailed, Field(discriminator="event")
]


class PartReview(_Model):
    """One part the self-checks flagged, with every check it failed."""

    ref: str
    warnings: list[PartWarning]


class ProjectResult(_Model):
    """One project as it went onto the master list."""

    label: str = Field(description="The name with its build: 'Muffin Fuzz (Ram)'.")
    name: str
    reviews: list[PartReview]
    ocr_reads: list[PartWarning] = Field(description="Parts read by OCR, to check.")
    options: list[str]
    errors: list[str] = Field(description="Why it couldn't be read, in plain words.")
    bugs: list[BugReport] = Field(description="Bugs in Star Ground, to report.")


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
    errors: list[str]


class MasterListResult(_Model):
    """Every project's parts, merged, with what to buy."""

    projects: list[ProjectResult]
    shopping_list: list[ShoppingRow]
    stats: Stats
    ignored_lines: list[str] = Field(
        description="Skipped lines that looked like parts."
    )
    has_stock: bool


class Problem(_Model):
    """Why a request couldn't be served."""

    message: str


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

    code: str = Field(max_length=16, description="The bug's `code`.")
    document: str = Field(
        max_length=2048,
        description="What was being read: the upload's name or the link. Never "
        "the document itself.",
    )
    detail: str = Field(max_length=20_000, description="The bug's `detail`.")
    comment: str = Field("", max_length=5000, description="Anything the builder adds.")


class Health(_Model):
    """The service is up."""

    status: Literal["ok"] = "ok"
