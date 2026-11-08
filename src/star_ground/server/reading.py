"""Turns a request's documents into readings and its projects into a master list.

Uploads and links are read through the instance's cache (`ReadingCache`),
so sending a document again costs a lookup, not another reading. OCR runs
only when asked, within the client's hourly allowance and in its turn.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import assert_never

from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from star_ground.api import (
    Correction,
    MasterList,
    Project,
    ProjectData,
    document_key,
    generate_master_list,
    read_document,
    read_stock,
)
from star_ground.bom_lib import (
    FileUpload,
    InputMethod,
    ParseResult,
    make_component_key,
)
from star_ground.bom_lib.errors import OcrRefusedError
from star_ground.bom_lib.ocr import OcrGuard, ocr_guard
from star_ground.server.models import (
    DocumentsMissing,
    MasterListRequest,
    PasteProject,
    Problem,
    ProjectIn,
    ReadEvent,
    ReadQueued,
    ReadStarted,
    UploadProject,
    UrlProject,
)
from star_ground.server.services import RefusedError, Services


def ocr_on(services: Services, asked: bool) -> bool:
    """Whether a read asked to run OCR will: the server may have it off."""
    return asked and services.ocr


def cache_key(
    services: Services, method: InputMethod, data: ProjectData, ocr: bool
) -> str | None:
    """The reading cache's key for a document, read with or without OCR."""
    key = document_key(method, data)
    return f"{key}|ocr" if key and ocr_on(services, ocr) else key


def _ocr_guard(
    services: Services, client: str, report: Callable[[ReadEvent], None] | None
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


def read_cached(
    services: Services,
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
        OcrRefusedError: The document needs OCR and the client has used its
            hourly allowance.
    """
    key = cache_key(services, method, data, ocr)
    if key is None:
        raise ValueError(f"{method} isn't read ahead of time")
    reading = services.cache.get(key)
    if reading is None:
        token = ocr_guard.set(_ocr_guard(services, client, report))
        try:
            reading = read_document(method, data, ocr=ocr_on(services, ocr))
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
    services: Services, project: UploadProject, uploads: Mapping[str, FileUpload]
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


def _project(
    services: Services,
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
        corrections=tuple(
            Correction(c.ref, make_component_key(c.category, c.value))
            for c in project.corrections
        ),
    )
    match project:
        case PasteProject():
            return replace(base, method=InputMethod.PASTE_TEXT, data=project.text)
        case UrlProject():
            reading = read_cached(
                services, client, InputMethod.FROM_URL, project.url, project.use_ocr
            )
            return replace(
                base, method=InputMethod.FROM_URL, data=project.url, reading=reading
            )
        case UploadProject():
            upload = _upload_for(services, project, uploads)
            reading = read_cached(
                services, client, InputMethod.UPLOAD_FILE, upload, project.use_ocr
            )
            return replace(
                base, method=InputMethod.UPLOAD_FILE, data=upload, reading=reading
            )
        case _:
            assert_never(project)


def upload(file: UploadFile) -> FileUpload:
    """A request's file part, as the backend takes uploads."""
    return FileUpload(file.filename or "upload", file.file.read())


def _uploads(files: Sequence[UploadFile]) -> dict[str, FileUpload]:
    uploads: dict[str, FileUpload] = {}
    for f in files:
        file = upload(f)
        if file.name in uploads:
            raise RefusedError(
                422, Problem(message=f"Two files are named '{file.name}'.")
            )
        uploads[file.name] = file
    return uploads


def parse_request(raw: str) -> MasterListRequest:
    """A master list request sent as a form field's JSON.

    Raises:
        RequestValidationError: The JSON isn't a `MasterListRequest`.
    """
    try:
        return MasterListRequest.model_validate_json(raw)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e


def master_list(
    services: Services,
    client: str,
    request: MasterListRequest,
    files: Sequence[UploadFile],
    stock: UploadFile | None,
) -> MasterList:
    """Reads the request's projects and merges them into the master list.

    Raises:
        RefusedError: An upload is named twice or not sent, or the cache no
            longer holds documents the request names (409).
    """
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
                message="This server no longer holds some uploads; send them as files.",
                documents=missing,
            ),
        )
    return generate_master_list(projects, read_stock(upload(stock)) if stock else None)
