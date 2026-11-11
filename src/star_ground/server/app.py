"""The HTTP API over the backend (`star_ground.api`), for the web frontend.

Every request carries what it needs: projects, uploads and stock arrive with
each master list or export request, and the browser keeps the user's state.
Uploads and links are read through a cache (`ReadingCache`), so sending a
document again costs a lookup, not another reading.

Run it with `just serve-api`, or `uvicorn star_ground.server:app`. Given the built
website (`Settings.site_dir`), it serves that too, at `/`, so one process is a
whole Star Ground: the Docker image runs it that way.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from star_ground.bom_lib.errors import (
    BuildNotChosenError,
    ErrorKind,
    StarGroundError,
    record_bug,
)
from star_ground.server.limits import BodySizeLimit
from star_ground.server.models import (
    BuildNotChosen,
    MasterListRequest,
    Problem,
    ReadEvent,
)
from star_ground.server.routes import documents, feedback, master_list, service
from star_ground.server.services import RefusedError, Services, Settings

_STATUS: dict[ErrorKind, int] = {
    ErrorKind.OCR_REFUSED: 429,
    ErrorKind.READING_REFUSED: 429,
    ErrorKind.BUILD_NOT_CHOSEN: 422,
}
"""The HTTP status a `StarGroundError` that ends a request answers with; any
kind not listed answers 422."""

_REQUEST_BUG = Problem(
    kind=ErrorKind.BUG,
    message="Star Ground hit a bug and couldn't finish this.",
    hint="Sending a report helps get it fixed.",
)
"""What a bug outside a reading answers, with its code added."""


class _BugBoundary:
    """ASGI middleware that answers a bug in any route with a report.

    The read route records its own (`bug_reading`): once its stream has
    started, there's no answer left to give.

    Args:
        app: The application to wrap.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serves the request, answering a bug before the response starts."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if started:
                raise
            bug = record_bug(f"answering {scope['method']} {scope['path']}")
            body = _REQUEST_BUG.model_copy(update={"bug_code": bug.code})
            await JSONResponse(body.model_dump(), status_code=500)(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the API.

    Args:
        settings: The instance's configuration; defaults if omitted.

    Returns:
        The FastAPI application.
    """
    settings = settings or Settings()
    app = FastAPI(
        title="Star Ground API",
        summary="Merges guitar pedal build documents into one shopping list.",
        version="0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.services = Services.for_settings(settings)
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
    # Inside CORS, so a bug's answer carries the headers a browser needs to
    # read it; outside the body limit, whose refusal isn't a bug.
    app.add_middleware(_BugBoundary)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
            expose_headers=["content-disposition"],
        )

    @app.exception_handler(RefusedError)
    async def _refused(request: Request, exc: RefusedError) -> JSONResponse:
        return JSONResponse(exc.body.model_dump(), status_code=exc.status)

    @app.exception_handler(StarGroundError)
    async def _explained(request: Request, exc: StarGroundError) -> JSONResponse:
        body = (
            BuildNotChosen(
                kind=exc.kind,
                message=exc.message,
                hint=exc.hint,
                projects=exc.projects,
            )
            if isinstance(exc, BuildNotChosenError)
            else Problem(kind=exc.kind, message=exc.message, hint=exc.hint)
        )
        return JSONResponse(body.model_dump(), status_code=_STATUS.get(exc.kind, 422))

    for routes in (service, documents, master_list, feedback):
        app.include_router(routes.router)

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
