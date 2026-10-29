"""The HTTP API over the backend (`src.api`), for the web frontend.

Every request carries what it needs: projects, uploads and stock arrive with
each master list or export request, and the browser keeps the user's state.
Uploads and links are read through a cache (`ReadingCache`), so sending a
document again costs a lookup, not another reading.

Run it with `just serve-api`, or `uvicorn src.server:app`. Given the built
website (`Settings.site_dir`), it serves that too, at `/`, so one process is a
whole Star Ground: the Docker image runs it that way.
"""

import contextvars
import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, assert_never

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, TypeAdapter, ValidationError

from src.api import (
    MasterList,
    Project,
    ProjectData,
    document_key,
    file_title,
    generate_master_list,
    offered,
    parts_to_review,
    read_document,
    read_stock,
)
from src.bom_lib import (
    BOM_PRESETS,
    FeedbackRating,
    FileUpload,
    InputMethod,
    ParseResult,
    ParseWarning,
    ParseWarningKind,
    PcbShop,
    ShoppingListRow,
    get_preset_metadata,
    get_residual_report,
    parse_preset_key,
)
from src.bom_lib.errors import (
    BUG_MESSAGE,
    Bug,
    BuildNotChosenError,
    OcrRefusedError,
    record_bug,
)
from src.bom_lib.ocr import OcrGuard, needs_ocr, ocr_guard
from src.exporters import (
    generate_shopping_list_csv,
    generate_stock_update_csv,
    generate_supplier_cart_csv,
)
from src.server.limits import BodySizeLimit, OcrQueue, RateLimiter, ReadingCache
from src.server.models import (
    BugReport,
    BuildNotChosen,
    DocumentReading,
    DocumentsMissing,
    ExportKind,
    FeedbackIn,
    Health,
    LinkFormat,
    MasterListRequest,
    MasterListResult,
    OcrReading,
    PartReview,
    PartWarning,
    PasteProject,
    Preset,
    PresetCatalog,
    PresetProject,
    Problem,
    ProjectIn,
    ProjectResult,
    ReadDone,
    ReadEvent,
    ReadFailed,
    ReadQueued,
    ReadStarted,
    ReportIn,
    ShoppingRow,
    Stats,
    UploadProject,
    UrlProject,
)

logger = logging.getLogger(__name__)

type FeedbackSink = Callable[[FeedbackRating, str], None]
"""Records a rating and comment (the Google Sheet, in production)."""

type ReportSink = Callable[[ReportIn], None]
"""Records a builder's bug report (the sheet's Reports tab, in production)."""


@dataclass(frozen=True, slots=True)
class Settings:
    """How a server instance is configured.

    Attributes:
        cors_origins: Browser origins allowed to call the API (the GitHub
            Pages site); none when the site is served from the same origin.
        max_body_bytes: The largest request body accepted.
        requests_per_minute: Each client's limit on the API's working routes.
        ocr: Whether OCR runs at all; off, image parts lists are reported
            and never offered.
        ocr_per_hour: Each client's limit on documents read by OCR, which
            take an instance's cores for tens of seconds each.
        trust_proxy: Take the client's address from `X-Forwarded-For`, as
            set by the platform's proxy (Cloud Run).
        cache_bytes: The reading cache's budget (see `ReadingCache`).
        site_dir: The built website (`web/dist`), served at `/` beside the
            API; none when the site is hosted elsewhere (GitHub Pages).
    """

    cors_origins: tuple[str, ...] = ()
    max_body_bytes: int = 50_000_000
    requests_per_minute: int = 30
    ocr: bool = True
    ocr_per_hour: int = 10
    trust_proxy: bool = False
    cache_bytes: int = 200_000_000
    site_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        """Reads the settings from `STAR_GROUND_*` environment variables.

        Args:
            env: The environment.

        Returns:
            The settings, with defaults for anything unset.
        """
        defaults = cls()
        origins = env.get("STAR_GROUND_CORS_ORIGINS", "")
        site = env.get("STAR_GROUND_SITE_DIR")
        return cls(
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            requests_per_minute=int(
                env.get("STAR_GROUND_REQUESTS_PER_MINUTE", defaults.requests_per_minute)
            ),
            ocr=env.get("STAR_GROUND_OCR", "1") != "0",
            ocr_per_hour=int(
                env.get("STAR_GROUND_OCR_PER_HOUR", defaults.ocr_per_hour)
            ),
            trust_proxy=env.get("STAR_GROUND_TRUST_PROXY", "") == "1",
            site_dir=Path(site) if site else None,
        )


class FeedbackSheet:
    """Appends feedback to the Google Sheet, reconnecting hourly.

    Args:
        credentials: A Google service account's key, as its JSON fields.
    """

    def __init__(self, credentials: Mapping[str, Any]) -> None:
        self._credentials = credentials
        self._sheet: Any = None
        self._connected = 0.0
        self._reports: Any = None
        self._reports_at = 0.0
        self._lock = threading.Lock()

    def __call__(self, rating: FeedbackRating, comment: str) -> None:
        """Appends one rating and comment."""
        from src.feedback import connect_feedback_sheet, save_feedback

        with self._lock:
            if self._sheet is None or time.monotonic() - self._connected > 3600:
                self._sheet = connect_feedback_sheet(self._credentials)
                self._connected = time.monotonic()
            save_feedback(self._sheet, rating, comment)

    def report(self, report: ReportIn) -> None:
        """Appends one bug report to the Reports tab."""
        from src.feedback import connect_report_sheet, save_report

        with self._lock:
            if self._reports is None or time.monotonic() - self._reports_at > 3600:
                self._reports = connect_report_sheet(self._credentials)
                self._reports_at = time.monotonic()
            save_report(
                self._reports,
                report.code,
                report.document,
                report.comment,
                report.detail,
            )


def feedback_from_env(env: Mapping[str, str] = os.environ) -> FeedbackSheet | None:
    """The feedback sheet, if `STAR_GROUND_FEEDBACK_CREDENTIALS` holds a key.

    Args:
        env: The environment.

    Returns:
        The sink, or None when feedback isn't configured.
    """
    raw = env.get("STAR_GROUND_FEEDBACK_CREDENTIALS")
    return FeedbackSheet(json.loads(raw)) if raw else None


class RefusedError(Exception):
    """A request the API refuses, with the body to answer it with.

    Args:
        status: The HTTP status.
        body: The response body.
    """

    def __init__(self, status: int, body: Problem) -> None:
        super().__init__(body.message)
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class _Exports:
    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class _Services:
    settings: Settings
    limiter: RateLimiter
    ocr_limiter: RateLimiter
    ocr_queue: OcrQueue
    cache: ReadingCache
    feedback: FeedbackSink | None
    reports: ReportSink | None


def _client(request: Request, trust_proxy: bool) -> str:
    """The client's address; behind a proxy, the last hop it appended."""
    forwarded = request.headers.get("x-forwarded-for")
    if trust_proxy and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _ocr_guard(
    services: _Services, client: str, report: Callable[[ReadEvent], None] | None
) -> OcrGuard:
    """Runs a client's OCR in its turn, within the client's hourly limit.

    Only OCR waits here: a text read never queues behind another document's
    OCR. A cached reading never reaches OCR, so it costs no allowance.
    """

    @contextmanager
    def guard() -> Iterator[None]:
        if not services.ocr_limiter.allow(client):
            raise OcrRefusedError
        position = services.ocr_queue.queue_position()
        if report and position:
            report(ReadQueued(position=position))
        with services.ocr_queue.slot():
            if report and position:
                report(ReadStarted())
            yield

    return guard


def _ocr_on(services: _Services, asked: bool) -> bool:
    """Whether a read asked to run OCR will: the server may have it off."""
    return asked and services.settings.ocr


def _cache_key(
    services: _Services, method: InputMethod, data: ProjectData, ocr: bool
) -> str | None:
    key = document_key(method, data)
    return f"{key}|ocr" if key and _ocr_on(services, ocr) else key


def _read(
    services: _Services,
    client: str,
    method: InputMethod,
    data: ProjectData,
    ocr: bool,
    report: Callable[[ReadEvent], None] | None = None,
) -> ParseResult:
    """An upload or link's reading, from the cache or read now.

    OCR runs only when asked (`ocr`, the builder having accepted it) and the
    server allows it; the readings with and without it are cached apart.

    Raises:
        RefusedError: The document needs OCR and the client has used its
            hourly allowance.
    """
    key = _cache_key(services, method, data, ocr)
    if key is None:
        raise ValueError(f"{method} isn't read ahead of time")
    reading = services.cache.get(key)
    if reading is None:
        token = ocr_guard.set(_ocr_guard(services, client, report))
        try:
            reading = read_document(method, data, ocr=_ocr_on(services, ocr))
        except OcrRefusedError as e:
            raise RefusedError(429, Problem(message=str(e))) from e
        finally:
            ocr_guard.reset(token)
        if reading is None:
            raise ValueError(f"{method} isn't read ahead of time")
        services.cache.put(key, reading)
    return reading


class _MissingDocumentError(Exception):
    """An upload named by `document` that the reading cache no longer holds."""

    def __init__(self, document: str) -> None:
        super().__init__(document)
        self.document = document


def _upload_for(
    services: _Services, project: UploadProject, uploads: Mapping[str, FileUpload]
) -> FileUpload:
    """The project's upload: its file part, or rebuilt from a cached reading.

    A cached reading holds the document's bytes (`raw_content`), so a page can
    name an upload it sent before instead of sending it with every change.

    Raises:
        RefusedError: The named file part wasn't sent.
        _MissingDocumentError: The named document isn't cached here.
    """
    if project.file is not None and (upload := uploads.get(project.file)):
        return upload
    if project.document is None:
        raise RefusedError(
            422, Problem(message=f"No file named '{project.file}' was sent.")
        )
    document = project.document
    reading = services.cache.get(document) or services.cache.get(f"{document}|ocr")
    name = document.removeprefix("file:").rpartition(":")[0]
    if reading is None or reading.raw_content is None or not name:
        raise _MissingDocumentError(document)
    upload = FileUpload(name, reading.raw_content)
    if document_key(InputMethod.UPLOAD_FILE, upload) != document:
        raise _MissingDocumentError(document)
    return upload


def _bug(bug: Bug) -> BugReport:
    return BugReport(code=bug.code, detail=bug.detail)


def _document_reading(
    services: _Services,
    reading: ParseResult,
    method: InputMethod,
    data: ProjectData,
    ocr_run: bool,
) -> DocumentReading:
    ocr = reading.ocr
    return DocumentReading(
        document=document_key(method, data)
        if method == InputMethod.UPLOAD_FILE
        else None,
        title=reading.title,
        file_title=file_title(method, data),
        parts_found=reading.stats["parts_found"],
        builds=list(reading.variant_names),
        ocr_builds=list(offered(reading, use_ocr=True).variant_names),
        ocr=OcrReading(
            verdict=ocr.verdict,
            message=ocr.message,
            parts_read=ocr.parts_read,
            suspect=ocr.suspect,
            pages=list(ocr.pages),
            has_parts=ocr.has_parts,
        )
        if ocr is not None
        else None,
        ocr_offered=services.settings.ocr and not ocr_run and needs_ocr(reading),
        options=[str(o) for o in reading.options],
        errors=list(reading.stats["errors"]),
        bugs=[_bug(b) for b in reading.bugs],
    )


def _project(
    services: _Services,
    client: str,
    project: ProjectIn,
    uploads: Mapping[str, FileUpload],
) -> Project:
    """The request's project as the backend takes it."""
    base = Project(
        name=project.name,
        count=project.count,
        variant=project.variant,
        use_ocr=project.use_ocr,
    )
    match project:
        case PasteProject():
            return replace(base, method=InputMethod.PASTE_TEXT, data=project.text)
        case PresetProject():
            preset = BOM_PRESETS.get(project.preset)
            parsed = parse_preset_key(project.preset)
            if preset is None or parsed is None:
                raise RefusedError(
                    422, Problem(message=f"There's no preset '{project.preset}'.")
                )
            shop = PcbShop(parsed[0]) if parsed[0] in PcbShop else None
            return replace(
                base,
                # A blank name takes the preset's, as the library lists it.
                name=project.name or parsed[2],
                method=InputMethod.PRESET,
                data=preset["bom_text"],
                source_path=preset.get("source_path"),
                source_url=preset.get("source_url"),
                pcb_shop=shop,
            )
        case UrlProject():
            reading = _read(
                services, client, InputMethod.FROM_URL, project.url, project.use_ocr
            )
            return replace(
                base, method=InputMethod.FROM_URL, data=project.url, reading=reading
            )
        case UploadProject():
            upload = _upload_for(services, project, uploads)
            reading = _read(
                services, client, InputMethod.UPLOAD_FILE, upload, project.use_ocr
            )
            return replace(
                base, method=InputMethod.UPLOAD_FILE, data=upload, reading=reading
            )
        case _:
            assert_never(project)


def _warning(w: ParseWarning) -> PartWarning:
    return PartWarning(kind=w.kind, ref=w.ref, message=w.message)


def _row(row: ShoppingListRow) -> ShoppingRow:
    return ShoppingRow(
        origin=row["Origin"],
        category=row["Category"],
        part=row["Part"],
        bom_qty=row["BOM Qty"],
        included=row["Included"],
        in_stock=row.get("In Stock"),
        net_need=row.get("Net Need"),
        buy_qty=row["Buy Qty"],
        notes=row["Notes"],
        search_term=row["Search Term"],
        supplier_url=row["Tayda_Link"],
    )


def _result(master: MasterList, request: MasterListRequest) -> MasterListResult:
    projects = []
    for p in master.projects:
        ocr = [w for w in p.warnings if w.kind == ParseWarningKind.OCR_READ]
        checks = [w for w in p.warnings if w.kind != ParseWarningKind.OCR_READ]
        projects.append(
            ProjectResult(
                label=p.label,
                name=p.name,
                reviews=[
                    PartReview(ref=ref, warnings=[_warning(w) for w in ws])
                    for ref, ws in parts_to_review(checks).items()
                ],
                ocr_reads=[_warning(w) for w in ocr],
                options=list(p.options),
                errors=list(p.errors),
                bugs=[_bug(b) for b in p.bugs],
            )
        )
    stats = master.stats
    return MasterListResult(
        projects=projects,
        shopping_list=[
            _row(r)
            for r in master.shopping_list(request.show_hardware, request.show_extras)
        ],
        stats=Stats(
            lines_read=stats["lines_read"],
            parts_found=stats["parts_found"],
            errors=list(stats["errors"]),
        ),
        ignored_lines=get_residual_report(stats),
        has_stock=master.stock is not None,
    )


def _sse(event: BaseModel) -> str:
    name = event.model_dump()["event"]
    return f"event: {name}\ndata: {event.model_dump_json()}\n\n"


def _upload(file: UploadFile) -> FileUpload:
    return FileUpload(file.filename or "upload", file.file.read())


def _parse_request(raw: str) -> MasterListRequest:
    try:
        return MasterListRequest.model_validate_json(raw)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e


def _uploads(files: Sequence[UploadFile]) -> dict[str, FileUpload]:
    uploads: dict[str, FileUpload] = {}
    for f in files:
        upload = _upload(f)
        if upload.name in uploads:
            raise RefusedError(
                422, Problem(message=f"Two files are named '{upload.name}'.")
            )
        uploads[upload.name] = upload
    return uploads


_EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"text/csv": {}, "application/zip": {}},
        "description": "The file, as an attachment.",
    },
    409: {"model": DocumentsMissing},
    422: {"model": BuildNotChosen | Problem},
}


def create_app(
    settings: Settings | None = None,
    feedback: FeedbackSink | None = None,
    reports: ReportSink | None = None,
) -> FastAPI:
    """Builds the API.

    Args:
        settings: The instance's configuration; defaults if omitted.
        feedback: Where feedback goes; the feedback route answers 503
            without one.
        reports: Where bug reports go; the reports route answers 503
            without one.

    Returns:
        The FastAPI application.
    """
    settings = settings or Settings()
    services = _Services(
        settings=settings,
        limiter=RateLimiter(settings.requests_per_minute),
        ocr_limiter=RateLimiter(settings.ocr_per_hour, window=3600),
        ocr_queue=OcrQueue(1),
        cache=ReadingCache(settings.cache_bytes),
        feedback=feedback,
        reports=reports,
    )
    app = FastAPI(
        title="Star Ground API",
        summary="Merges guitar pedal build documents into one shopping list.",
        version="0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
            expose_headers=["content-disposition"],
        )

    @app.exception_handler(RefusedError)
    async def _problem(request: Request, exc: RefusedError) -> JSONResponse:
        return JSONResponse(exc.body.model_dump(), status_code=exc.status)

    def limited(request: Request) -> str:
        """Counts the request against its client's limit; returns the client."""
        client = _client(request, settings.trust_proxy)
        if not services.limiter.allow(client):
            raise RefusedError(
                429, Problem(message="Too many requests. Try again in a minute.")
            )
        return client

    def master_list(
        client: str,
        request: MasterListRequest,
        files: Sequence[UploadFile],
        stock: UploadFile | None,
    ) -> MasterList:
        uploads = _uploads(files)
        projects: list[Project] = []
        missing: list[str] = []
        for p in request.projects:
            try:
                projects.append(_project(services, client, p, uploads))
            except _MissingDocumentError as e:
                missing.append(e.document)
        if missing:
            raise RefusedError(
                409,
                DocumentsMissing(
                    message="This server no longer holds some uploads; send them "
                    "as files.",
                    documents=missing,
                ),
            )
        try:
            return generate_master_list(
                projects, read_stock(_upload(stock)) if stock else None
            )
        except BuildNotChosenError as e:
            raise RefusedError(
                422, BuildNotChosen(message=str(e), projects=e.projects)
            ) from e

    @app.get("/api/health", tags=["service"])
    def health() -> Health:
        """Answers once the server is up; the frontend calls it to wake it."""
        return Health()

    @app.get("/api/presets", tags=["presets"])
    def presets() -> PresetCatalog:
        """The preset library, for browsing by source and category."""
        catalog = get_preset_metadata()
        return PresetCatalog(
            sources=catalog.sources,
            categories=catalog.categories,
            presets=[
                Preset(
                    key=e["full_key"],
                    source=e["source"],
                    category=e["category"],
                    name=e["name"],
                )
                for e in catalog.lookup
            ],
        )

    @app.post(
        "/api/documents/read",
        tags=["documents"],
        response_class=StreamingResponse,
        responses={
            200: {
                "content": {
                    "text/event-stream": {
                        "schema": {"$ref": "#/components/schemas/ReadEvent"}
                    }
                },
                "description": "A `reading` event; `queued` then `reading` "
                "again if the document waits for its turn at OCR; then "
                "`result` or `error`. Each event's data is one `ReadEvent`.",
            }
        },
    )
    def read(
        request: Request,
        file: Annotated[UploadFile | None, File()] = None,
        url: Annotated[str | None, Form(max_length=2048)] = None,
        ocr: Annotated[
            bool,
            Form(description="Read image pages by OCR; send once the builder accepts."),
        ] = False,
    ) -> StreamingResponse:
        """Reads an upload or link ahead of building the master list.

        The reading says which builds a multi-circuit document offers, and
        whether OCR could read parts from image pages (`ocr_offered`). OCR
        runs only when asked (`ocr`) and takes seconds a page, so progress
        streams as server-sent events.
        """
        client = limited(request)
        if (file is None) == (url is None):
            raise RefusedError(422, Problem(message="Send either a file or a url."))
        method, data = (
            (InputMethod.UPLOAD_FILE, _upload(file))
            if file is not None
            else (InputMethod.FROM_URL, str(url).strip())
        )

        def events() -> Iterator[str]:
            key = _cache_key(services, method, data, ocr)
            ocr_run = _ocr_on(services, ocr)
            cached = services.cache.get(key) if key else None
            if cached is not None:
                yield _sse(
                    ReadDone(
                        reading=_document_reading(
                            services, cached, method, data, ocr_run
                        )
                    )
                )
                return
            # The reading runs on its own thread, so the OCR queue's events
            # stream while it waits for a turn.
            yield _sse(ReadStarted())
            progress: queue.SimpleQueue[ReadEvent] = queue.SimpleQueue()
            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(
                    contextvars.copy_context().run,
                    _read,
                    services,
                    client,
                    method,
                    data,
                    ocr,
                    progress.put,
                )
                while not job.done() or not progress.empty():
                    try:
                        yield _sse(progress.get(timeout=0.2))
                    except queue.Empty:
                        continue
                try:
                    reading = job.result()
                except RefusedError as e:
                    yield _sse(ReadFailed(message=e.body.message, bug=None))
                    return
                except Exception:
                    bug = record_bug(f"reading {file_title(method, data) or key}")
                    yield _sse(ReadFailed(message=BUG_MESSAGE, bug=_bug(bug)))
                    return
            yield _sse(
                ReadDone(
                    reading=_document_reading(services, reading, method, data, ocr_run)
                )
            )

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post(
        "/api/master-list",
        tags=["master list"],
        responses={
            409: {"model": DocumentsMissing},
            422: {"model": BuildNotChosen | Problem},
        },
    )
    def build_master_list(
        http: Request,
        request: Annotated[str, Form(description="A `MasterListRequest`, as JSON.")],
        files: Annotated[list[UploadFile] | None, File()] = None,
        stock: Annotated[UploadFile | None, File()] = None,
    ) -> MasterListResult:
        """Merges the projects into one master list.

        Uploads are sent as `files`, named as their projects' `file` says;
        `stock` is the builder's stock CSV (columns Category, Part, Qty).
        """
        client = limited(http)
        parsed = _parse_request(request)
        return _result(master_list(client, parsed, files or [], stock), parsed)

    @app.post("/api/exports/{kind}", tags=["master list"], responses=_EXPORT_RESPONSES)
    def export(
        http: Request,
        kind: ExportKind,
        request: Annotated[str, Form(description="A `MasterListRequest`, as JSON.")],
        files: Annotated[list[UploadFile] | None, File()] = None,
        stock: Annotated[UploadFile | None, File()] = None,
        links: LinkFormat = LinkFormat.PLAIN,
    ) -> Response:
        """Exports the master list as a CSV or ZIP, from the same request body.

        `links` sets how the shopping list (alone or in the build pack)
        writes supplier links: as plain URLs, or as Excel `HYPERLINK`s.
        """
        client = limited(http)
        parsed = _parse_request(request)
        master = master_list(client, parsed, files or [], stock)
        rows = master.shopping_list(parsed.show_hardware, parsed.show_extras)
        excel = links == LinkFormat.EXCEL
        match kind:
            case ExportKind.SHOPPING_LIST:
                out = _Exports(
                    generate_shopping_list_csv(rows, use_excel_formulas=excel),
                    "text/csv",
                    "shopping-list.csv",
                )
            case ExportKind.SUPPLIER_CART:
                out = _Exports(
                    generate_supplier_cart_csv(rows), "text/csv", "supplier-cart.csv"
                )
            case ExportKind.STOCK_UPDATE:
                out = _Exports(
                    generate_stock_update_csv(rows), "text/csv", "stock-update.csv"
                )
            case ExportKind.FIELD_MANUALS:
                out = _Exports(
                    master.pdf_bundle(), "application/zip", "field-manuals.zip"
                )
            case ExportKind.BUILD_PACK:
                out = _Exports(
                    master.build_pack(
                        generate_shopping_list_csv(rows, use_excel_formulas=excel),
                        generate_stock_update_csv(rows),
                    ),
                    "application/zip",
                    "build-pack.zip",
                )
        return Response(
            out.content,
            media_type=out.media_type,
            headers={"content-disposition": f'attachment; filename="{out.filename}"'},
        )

    @app.post(
        "/api/feedback",
        tags=["feedback"],
        status_code=204,
        responses={503: {"model": Problem}},
    )
    def send_feedback(http: Request, body: FeedbackIn) -> None:
        """Records a rating and comment from the app."""
        limited(http)
        if services.feedback is None:
            raise RefusedError(
                503, Problem(message="Feedback isn't set up on this server.")
            )
        services.feedback(list(FeedbackRating)[body.rating - 1], body.comment)

    @app.post(
        "/api/reports",
        tags=["feedback"],
        status_code=204,
        responses={503: {"model": Problem}},
    )
    def send_report(http: Request, body: ReportIn) -> None:
        """Records a builder's report of a bug, with what was being read.

        Never the document itself: an upload's name, or a link.
        """
        limited(http)
        if services.reports is None:
            raise RefusedError(
                503, Problem(message="Reports aren't set up on this server.")
            )
        services.reports(body)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                summary=app.summary,
                routes=app.routes,
            )
            # The read stream's events and the master list's request (sent as
            # a form field's JSON) aren't a route's body, so FastAPI doesn't
            # list them; add them for the frontend's types.
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            extras: dict[str, TypeAdapter[Any]] = {
                "ReadEvent": TypeAdapter(ReadEvent),
                "MasterListRequest": TypeAdapter(MasterListRequest),
            }
            for name, adapter in extras.items():
                extra = adapter.json_schema(ref_template="#/components/schemas/{model}")
                components.update(extra.pop("$defs", {}))
                components[name] = extra
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    # Mounted last, so the API's routes match first.
    if settings.site_dir:
        app.mount("/", StaticFiles(directory=settings.site_dir, html=True), name="site")
    return app
