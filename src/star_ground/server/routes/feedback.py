"""Builders' reports of bugs and of parts read wrong, kept in the log."""

import logging

from fastapi import APIRouter

from star_ground.bom_lib import FeedbackKind
from star_ground.server.models import PartReportIn, ReportIn
from star_ground.server.services import Client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


@router.post("/api/reports", status_code=204)
def send_report(client: Client, body: ReportIn) -> None:
    """Records a builder's report of a bug, with what was being read.

    Never the document itself: an upload's name, or a link. The report is
    logged under the bug's code, beside the bug's own entry and traceback,
    which Error Reporting groups.
    """
    logger.warning(
        "Report of bug %s",
        body.code,
        extra={
            "bug_code": body.code,
            "feedback": {
                "kind": FeedbackKind.BUG,
                "document": body.document,
                "comment": body.comment,
            },
        },
    )


@router.post("/api/part-reports", status_code=204)
def send_part_report(client: Client, body: PartReportIn) -> None:
    """Records a builder's report that a part was read wrong.

    It names the document by its link (or an upload's name and hash) and
    says where the part was read, so the document can join the parser's
    regression corpus. Never the document itself. It's logged, and the log
    export keeps it in BigQuery (see `deploy/README.md`).
    """
    at = body.location
    logger.info(
        "Report of %s read as %s",
        body.ref,
        body.read,
        extra={
            "feedback": {
                "kind": FeedbackKind.PART,
                "document": body.document,
                "ref": body.ref,
                "read": body.read,
                "correction": body.correction,
                # Pages from 1, as a PDF viewer numbers them; the box in
                # points from the page's top-left.
                "page": at.page + 1 if at else None,
                "box": [at.x0, at.top, at.x1, at.bottom] if at else None,
                "comment": body.comment,
            }
        },
    )
