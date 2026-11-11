"""Reading a document ahead of the master list, and passing on a link's PDF."""

import contextvars
import queue
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from star_ground.api import ProjectData, document_key, file_title
from star_ground.bom_lib import InputMethod, ParseResult
from star_ground.bom_lib.errors import StarGroundError, bug_reading
from star_ground.server.models import (
    Problem,
    ReadDone,
    ReadEvent,
    ReadFailed,
    ReadStarted,
)
from star_ground.server.reading import cache_key, ocr_on, read_cached, upload
from star_ground.server.responses import document_reading, problem, sse
from star_ground.server.services import Limit, RefusedError, Services, ServicesDep

router = APIRouter(tags=["documents"])


@router.post(
    "/api/documents/read",
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
    limit: Limit,
    services: ServicesDep,
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
    client = limit()
    if (file is None) == (url is None):
        raise RefusedError(422, Problem(message="Send either a file or a url."))
    method, data = (
        (InputMethod.UPLOAD_FILE, upload(file))
        if file is not None
        else (InputMethod.FROM_URL, str(url).strip())
    )
    return StreamingResponse(
        read_events(services, client, method, data, ocr),
        media_type="text/event-stream",
    )


def read_events(
    services: Services,
    client: str,
    method: InputMethod,
    data: ProjectData,
    ocr: bool,
) -> Generator[str]:
    """A document's reading as server-sent events, from the cache or read now.

    The reading runs on its own thread, so the OCR queue's events stream
    while it waits for a turn. A builder who leaves mid-read closes the
    stream; closing never waits for the reading, which finishes on its thread,
    releases what it holds (its OCR turn) and is cached for the next request.
    """
    ocr_run = ocr_on(services, ocr)

    def done(reading: ParseResult) -> str:
        return sse(
            ReadDone(
                reading=document_reading(
                    reading, method, data, ocr_available=services.ocr and not ocr_run
                )
            )
        )

    key = cache_key(services, method, data, ocr)
    cached = services.cache.get(key) if key else None
    if cached is not None:
        yield done(cached)
        return
    yield sse(ReadStarted())
    progress: queue.SimpleQueue[ReadEvent] = queue.SimpleQueue()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        job = pool.submit(
            contextvars.copy_context().run,
            read_cached,
            services,
            client,
            method,
            data,
            ocr,
            progress.put,
        )
        while not job.done() or not progress.empty():
            try:
                yield sse(progress.get(timeout=0.2))
            except queue.Empty:
                continue
        try:
            reading = job.result()
        except StarGroundError as e:
            yield sse(ReadFailed(kind=e.kind, message=e.message, hint=e.hint))
            return
        except Exception:
            # The response has started, so `_BugBoundary` can't answer.
            error = bug_reading(f"reading {file_title(method, data) or key}")
            yield sse(ReadFailed(**problem(error).model_dump()))
            return
    finally:
        pool.shutdown(wait=False)
    yield done(reading)


@router.get(
    "/api/documents/pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The PDF."},
        404: {"model": Problem},
    },
)
def document_pdf(
    limit: Limit,
    services: ServicesDep,
    url: Annotated[str, Query(max_length=2048)],
) -> Response:
    """A linked document's PDF, for the page to draw beside its parts.

    A browser can't fetch a publisher's file itself, so the API passes on
    the copy it read. Nothing is kept beyond the reading cache.
    """
    client = limit()
    url = url.strip()
    key = document_key(InputMethod.FROM_URL, url)
    reading = (
        services.cache.get(f"{key}|ocr")
        or services.cache.get(str(key))
        or read_cached(services, client, InputMethod.FROM_URL, url, ocr=False)
    )
    content = reading.raw_content
    if content is None or not content.startswith(b"%PDF"):
        raise RefusedError(
            404,
            Problem(message="This link isn't a PDF, so there's nothing to show."),
        )
    return Response(content, media_type="application/pdf")
