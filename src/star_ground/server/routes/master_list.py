"""Building the master list, exporting its files, and reading a typed part."""

import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response

from star_ground import version
from star_ground.api import read_correction
from star_ground.exports.pack import PackContents
from star_ground.exports.render import ExportKind, render_export
from star_ground.server.models import (
    BuildNotChosen,
    DocumentsMissing,
    LinkFormat,
    MasterListResult,
    PartIn,
    PartOut,
    Problem,
)
from star_ground.server.reading import master_list, parse_request
from star_ground.server.responses import master_list_result
from star_ground.server.services import Limit, ServicesDep

router = APIRouter(tags=["master list"])

_EXPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"text/csv": {}, "application/pdf": {}, "application/zip": {}},
        "description": "The file, as an attachment.",
    },
    409: {"model": DocumentsMissing},
    422: {"model": BuildNotChosen | Problem},
}


@router.post(
    "/api/master-list",
    responses={
        409: {"model": DocumentsMissing},
        422: {"model": BuildNotChosen | Problem},
    },
)
def build_master_list(
    limit: Limit,
    services: ServicesDep,
    request: Annotated[str, Form(description="A `MasterListRequest`, as JSON.")],
    files: Annotated[list[UploadFile] | None, File()] = None,
    stock: Annotated[UploadFile | None, File()] = None,
) -> MasterListResult:
    """Merges the projects into one master list.

    Uploads are sent as `files`, named as their projects' `file` says;
    `stock` is the builder's stock CSV (columns Category, Part, Qty).
    """
    client = limit()
    parsed = parse_request(request)
    master = master_list(services, client, parsed, files or [], stock)
    return master_list_result(master, parsed)


@router.post("/api/exports/{kind}", responses=_EXPORT_RESPONSES)
def export(
    limit: Limit,
    services: ServicesDep,
    kind: ExportKind,
    request: Annotated[str, Form(description="A `MasterListRequest`, as JSON.")],
    files: Annotated[list[UploadFile] | None, File()] = None,
    stock: Annotated[UploadFile | None, File()] = None,
    links: LinkFormat = LinkFormat.PLAIN,
) -> Response:
    """Exports the master list as a CSV, PDF or ZIP, from the same request body.

    `links` sets how the shopping list (alone or in the build pack)
    writes supplier links: as plain URLs, or as Excel `HYPERLINK`s.
    """
    client = limit()
    parsed = parse_request(request)
    master = master_list(services, client, parsed, files or [], stock)
    contents = PackContents(
        kit=master.kit,
        projects=master.projects,
        lines=master.shopping_list(parsed.show_hardware, parsed.show_extras),
        stock=master.stock,
        excel_links=links == LinkFormat.EXCEL,
        made=datetime.date.today(),
        version=version(),
    )
    out = render_export(kind, contents)
    return Response(
        out.content,
        media_type=out.media_type,
        headers={"content-disposition": f'attachment; filename="{out.filename}"'},
    )


@router.post("/api/parts/read", responses={422: {"model": Problem}})
def read_part(limit: Limit, body: PartIn) -> PartOut:
    """Reads a value the builder typed for a part, as a parts list's row.

    A correction is read here as it's typed, so the master list request
    carries it as the list will show it.
    """
    limit()
    part = read_correction(body.ref, body.value, body.category)
    return PartOut(category=part.category, value=part.value)
