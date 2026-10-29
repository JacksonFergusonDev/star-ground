"""Problems Star Ground explains to the builder, and the bugs it can't.

Every problem a builder can act on is a `StarGroundError`: its kind, a message
saying what happened and a hint saying what to do, in plain words written for
them and shown as is. Any other exception is a bug in Star Ground. Where a
document is read, it's caught and recorded as a `Bug` (`record_bug`): the
builder is told plainly that their document isn't at fault and offered a
report, and the traceback goes to the log under the bug's code.
"""

import logging
import secrets
import traceback
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from typing import ClassVar

logger = logging.getLogger(__name__)


class ErrorKind(StrEnum):
    """What kind of problem stopped a reading or a request."""

    FETCH = "fetch"
    UNREADABLE_PDF = "unreadable_pdf"
    TOO_MANY_PAGES = "too_many_pages"
    BUILD_NOT_CHOSEN = "build_not_chosen"
    OCR_REFUSED = "ocr_refused"
    BUG = "bug"


@dataclass(frozen=True, slots=True)
class Bug:
    """A failure in Star Ground's own code, to report.

    Attributes:
        code: A short code naming this occurrence, shown to the builder and
            written to the log beside the traceback, so a report can be
            matched to it.
        detail: Star Ground's version, what it was doing, and the traceback.
            It stays on the server: the builder sees only the code.
    """

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReadError:
    """Why a document's parts list couldn't be read, for the builder.

    Attributes:
        kind: What kind of problem it is.
        message: What happened, in plain words.
        hint: What the builder can do about it; empty when there's nothing.
        bug: The bug, when the problem is one in Star Ground.
    """

    kind: ErrorKind
    message: str
    hint: str = ""
    bug: Bug | None = None


class StarGroundError(Exception):
    """A problem explained to the builder.

    `message` says what happened and `hint` what they can do about it,
    without code or jargon. `str(error)` is the message.

    Args:
        message: What happened.
        hint: What the builder can do about it.
    """

    kind: ClassVar[ErrorKind]

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_read_error(self) -> ReadError:
        """The problem, as a reading reports it."""
        return ReadError(self.kind, self.message, self.hint)


class FetchError(StarGroundError):
    """A link Star Ground won't fetch, or a response it can't read."""

    kind = ErrorKind.FETCH

    def __init__(
        self,
        message: str,
        hint: str = "Check the link, or download the document and upload it instead.",
    ) -> None:
        super().__init__(message, hint)


class UnreadablePdfError(StarGroundError):
    """A file that couldn't be opened as a PDF."""

    kind = ErrorKind.UNREADABLE_PDF

    def __init__(self) -> None:
        super().__init__(
            "This file couldn't be opened as a PDF.",
            "It may be damaged, password-protected or not a PDF at all: try "
            "downloading it again.",
        )


class TooManyPagesError(StarGroundError):
    """A PDF far longer than any build document.

    Args:
        pages: The PDF's page count.
        limit: The most pages a build document is read with.
    """

    kind = ErrorKind.TOO_MANY_PAGES

    def __init__(self, pages: int, limit: int) -> None:
        super().__init__(
            f"This PDF has {pages} pages. Build documents have at most {limit}, "
            "so it wasn't read.",
            "Check it's the build document for the pedal.",
        )


class BuildNotChosenError(StarGroundError):
    """A multi-circuit document's build was not chosen.

    Building the wrong variant buys the wrong parts, so there is no default.

    Attributes:
        projects: The names of the projects whose build must be chosen.
    """

    kind = ErrorKind.BUILD_NOT_CHOSEN

    def __init__(self, projects: list[str]) -> None:
        super().__init__(
            "Choose which build before generating: " + ", ".join(projects) + ".",
            "Each build of these documents needs different parts.",
        )
        self.projects = projects


class OcrRefusedError(StarGroundError):
    """The OCR guard declined to run OCR now (a server's per-client limit)."""

    kind = ErrorKind.OCR_REFUSED

    def __init__(self) -> None:
        super().__init__(
            "This document's parts list is an image, which Star Ground reads by "
            "OCR a limited number of times an hour for each person.",
            "Try again later.",
        )


BUG_MESSAGE = (
    "Star Ground hit a bug while reading this, so its parts aren't on the list."
)
"""What the builder is told about a bug in a reading, in place of its traceback."""

BUG_HINT = "It isn't a problem with your document. Sending a report helps get it fixed."
"""What the builder can do about a bug: report it."""


def _version() -> str:
    try:
        return version("star-ground")
    except PackageNotFoundError:
        return "unknown"


def record_bug(doing: str) -> Bug:
    """Records the exception being handled as a bug, and logs it.

    Call it from an `except` block.

    Args:
        doing: What Star Ground was doing, for the log ('reading Fuzz.pdf').

    Returns:
        The bug, with its code and detail.
    """
    code = secrets.token_hex(3).upper()
    detail = f"Star Ground {_version()}, {doing}\n\n{traceback.format_exc()}"
    logger.error("Bug %s: %s", code, detail)
    return Bug(code, detail)


def bug_reading(doing: str) -> ReadError:
    """Records the exception being handled as a bug in a reading.

    Call it from an `except` block.

    Args:
        doing: What Star Ground was reading, for the log ('reading Fuzz.pdf').

    Returns:
        The error the reading reports, carrying the bug.
    """
    return ReadError(ErrorKind.BUG, BUG_MESSAGE, BUG_HINT, record_bug(doing))
