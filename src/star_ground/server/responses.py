"""Turns the backend's readings and master list into the API's response models."""

from collections.abc import Sequence

from pydantic import BaseModel

from star_ground.api import (
    MasterList,
    ProjectData,
    document_key,
    file_title,
    offered,
    parts_to_review,
)
from star_ground.bom_lib import (
    CountedPart,
    InputMethod,
    Location,
    ParseResult,
    ParseWarning,
    ParseWarningKind,
    ReadPart,
    ShoppingLine,
    get_residual_report,
)
from star_ground.bom_lib.errors import ReadError
from star_ground.bom_lib.ocr import needs_ocr
from star_ground.bom_lib.stages import BuildStage, build_stage, stage_order
from star_ground.server.models import (
    BuildStep,
    CountedPartOut,
    DocumentReading,
    MasterListRequest,
    MasterListResult,
    OcrReading,
    PartReview,
    PartWarning,
    Problem,
    ProjectResult,
    ReadPartOut,
    ShoppingRow,
    SourceLocation,
    Stats,
)


def problem(error: ReadError) -> Problem:
    """A reading's error for the builder; its bug's traceback stays in the log."""
    return Problem(
        kind=error.kind,
        message=error.message,
        hint=error.hint,
        bug_code=error.bug.code if error.bug else None,
    )


def document_reading(
    reading: ParseResult,
    method: InputMethod,
    data: ProjectData,
    *,
    ocr_available: bool,
) -> DocumentReading:
    """What the page needs to know about a document read ahead of time.

    Args:
        reading: The reading.
        method: How the document was given.
        data: The upload or link.
        ocr_available: Whether OCR could still be offered: the server runs
            it, and this reading didn't.

    Returns:
        The reading, as the API answers it.
    """
    ocr = reading.ocr
    return DocumentReading(
        document=document_key(method, data)
        if method == InputMethod.UPLOAD_FILE
        else None,
        title=reading.title,
        file_title=file_title(method, data),
        parts_found=reading.stats.parts_found,
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
        ocr_offered=ocr_available and needs_ocr(reading),
        options=[str(o) for o in reading.options],
        errors=[problem(e) for e in reading.stats.errors],
    )


def _location(at: Location | None) -> SourceLocation | None:
    if at is None:
        return None
    return SourceLocation(
        page=at.page, x0=at.x0, top=at.top, x1=at.x1, bottom=at.bottom
    )


def _part(part: ReadPart) -> ReadPartOut:
    return ReadPartOut(
        ref=part.ref,
        category=part.category,
        value=part.value,
        location=_location(part.location),
    )


def _counted(part: CountedPart) -> CountedPartOut:
    return CountedPartOut(
        key=part.key, category=part.category, value=part.value, count=part.count
    )


def _build_order(
    parts: Sequence[ReadPart], counted: Sequence[CountedPart]
) -> list[BuildStep]:
    """A project's parts by soldering stage, lowest parts first."""
    stages: dict[BuildStage, BuildStep] = {}

    def step(stage: BuildStage) -> BuildStep:
        return stages.setdefault(stage, BuildStep(stage=stage, refs=[], counted=[]))

    for part in parts:
        step(build_stage(part.category, part.value)).refs.append(part.ref)
    for c in counted:
        step(build_stage(c.category, c.value)).counted.append(c.key)
    return sorted(stages.values(), key=lambda s: stage_order(s.stage))


def _warning(w: ParseWarning) -> PartWarning:
    return PartWarning(kind=w.kind, ref=w.ref, message=w.message)


def _row(row: ShoppingLine) -> ShoppingRow:
    return ShoppingRow(
        origin=row.origin,
        category=row.category.value,
        part=row.part,
        bom_qty=row.bom_qty,
        included=row.included,
        in_stock=row.in_stock,
        net_need=row.net_need,
        buy_qty=row.buy_qty,
        notes=row.notes,
        search_term=row.search_term,
        supplier_url=row.supplier_url,
    )


def master_list_result(
    master: MasterList, request: MasterListRequest
) -> MasterListResult:
    """The master list as the API answers it.

    Args:
        master: The master list.
        request: The request it was built from, for its display options.

    Returns:
        Each project's checks, parts and build order, and the shopping list.
    """
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
                errors=[problem(e) for e in p.errors],
                parts=[_part(part) for part in p.parts],
                counted=[_counted(c) for c in p.counted],
                build_order=_build_order(p.parts, p.counted),
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
            lines_read=stats.lines_read,
            parts_found=stats.parts_found,
        ),
        ignored_lines=get_residual_report(stats),
        has_stock=master.stock is not None,
    )


def sse(event: BaseModel) -> str:
    """One server-sent event, named by the model's `event` field."""
    name = event.model_dump()["event"]
    return f"event: {name}\ndata: {event.model_dump_json()}\n\n"
