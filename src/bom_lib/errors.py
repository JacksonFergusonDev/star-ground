"""Problems Star Ground explains to the builder, and the bugs it can't.

Every problem a builder can act on is a `StarGroundError`: its message is
written for them, in plain words, and shown as is. Any other exception is a
bug in Star Ground. Where a document is read, it's caught and recorded as a
`Bug` (`record_bug`): the builder is told plainly that their document isn't
at fault and offered a report, and the traceback goes to the log and the
report under the bug's code.
"""

import logging
import secrets
import traceback
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

logger = logging.getLogger(__name__)


class StarGroundError(Exception):
    """A problem explained to the builder.

    `str(error)` is the explanation, written for the builder: what happened
    and what they can do about it, without code or jargon.
    """


class FetchError(StarGroundError):
    """A link Star Ground won't fetch, or a response it can't read."""


class UnreadablePdfError(StarGroundError):
    """A file that couldn't be opened as a PDF."""

    def __init__(self) -> None:
        super().__init__(
            "This file couldn't be opened as a PDF. It may be damaged, "
            "password-protected or not a PDF at all: try downloading it again."
        )


class TooManyPagesError(StarGroundError):
    """A PDF far longer than any build document.

    Args:
        pages: The PDF's page count.
        limit: The most pages a build document is read with.
    """

    def __init__(self, pages: int, limit: int) -> None:
        super().__init__(
            f"This PDF has {pages} pages. Build documents have at most {limit}, "
            "so it wasn't read. Check it's the build document for the pedal."
        )


class BuildNotChosenError(StarGroundError):
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


class OcrRefusedError(StarGroundError):
    """The OCR guard declined to run OCR now (a server's per-client limit)."""

    def __init__(self) -> None:
        super().__init__(
            "This document's parts list is an image, which Star Ground reads by "
            "OCR a limited number of times an hour for each person. Try again "
            "later."
        )


BUG_MESSAGE = (
    "Star Ground hit a bug while reading this, so its parts aren't on the list. "
    "It isn't a problem with your document. Sending a report helps get it fixed."
)
"""What the builder is told about a bug, in place of its traceback."""


@dataclass(frozen=True, slots=True)
class Bug:
    """A failure in Star Ground's own code, to report.

    Attributes:
        code: A short code naming this occurrence, shown to the builder and
            written to the log beside the traceback, so a report can be
            matched to it.
        detail: Star Ground's version, what it was doing, and the traceback.
    """

    code: str
    detail: str


def _version() -> str:
    try:
        return version("star-ground")
    except PackageNotFoundError:
        return "unknown"


def record_bug(doing: str) -> Bug:
    """Records the exception being handled as a bug, and logs it.

    Call it from an `except` block.

    Args:
        doing: What Star Ground was doing, for the report ('reading Fuzz.pdf').

    Returns:
        The bug, with its code and detail.
    """
    code = secrets.token_hex(3).upper()
    detail = f"Star Ground {_version()}, {doing}\n\n{traceback.format_exc()}"
    logger.error("Bug %s: %s", code, detail)
    return Bug(code, detail)
