"""Opens a PDF and parses its pages within bounds, for every reader of one.

A build document is a few hundred kilobytes; a hostile upload can ask a
reader for gigabytes from far less. pdfminer decodes whatever a file says
when it's asked, so each place Star Ground opens a PDF goes through here:
`open_pdf` bounds what the structure decodes to as the file opens,
`list_pages` the page count, and `parse_page` what a page draws and holds.
"""

import logging
import numbers
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BufferedReader, BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO, NoReturn

from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFStream, resolve_all
from pdfminer.psparser import PSStackEntry
from pdfplumber.pdf import PDF
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
from pdfplumber.utils.pdfinternals import resolve_and_decode

from star_ground.bom_lib.errors import (
    CrowdedPageError,
    HeavyPageError,
    HeavyStreamError,
    StarGroundError,
    TooManyPagesError,
    UnreadablePdfError,
)
from star_ground.bom_lib.pdf.content import OpaqueStreamError, content_bytes, decoded

if TYPE_CHECKING:
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.page import Page

logger = logging.getLogger(__name__)

# The most pages a PDF may have. The longest corpus document has 34; a longer
# one isn't a build document, and every page costs seconds of shared CPU.
MAX_PDF_PAGES = 60
# The most characters a page may hold. The sample documents' busiest page
# holds 2,142, and a page of 6pt text about 10,000. Reading time grows with
# the characters (a page of 60,000 takes seconds), so a page past this is
# refused rather than read.
MAX_PAGE_CHARS = 20_000
# The most bytes of drawing instructions a page may run: its content streams
# and the forms they draw, a form counted every time it's drawn (see
# `content_bytes`). pdfminer decodes and reads them all before its characters
# can be counted, at about 1.8 KB of memory for each one drawn, and they
# compress a thousandfold or more. The sample documents' busiest page runs
# 75,622 bytes (LowTide, page 7), and their busiest text page draws a
# character for every 33 bytes, so a page at `MAX_PAGE_CHARS` written the
# same way would take about 660,000.
MAX_PAGE_CONTENT_BYTES = 1 << 20
# The most bytes all of a document's streams may decode to. pdfminer decodes
# the streams holding a file's objects and cross-reference table as it opens
# it, whole, and keeps what any stream decodes to. The sample documents'
# streams decode to 400,000 bytes in all; even a page at the bound above on
# each of 60 pages is 63 MiB.
MAX_DOCUMENT_STREAM_BYTES = 64 << 20


class _Budget:
    """What a document's streams may still decode to."""

    def __init__(self) -> None:
        self.left = MAX_DOCUMENT_STREAM_BYTES


class _GuardedStream(PDFStream):
    """A stream that refuses to decode past its document's budget."""

    def __init__(
        self,
        stream: PDFStream,
        budget: _Budget,
    ) -> None:
        super().__init__(stream.attrs, stream.rawdata or b"", stream.decipher)
        self._budget = budget

    def decode(self) -> None:
        """Decodes the stream, after finding it within the budget.

        Raises:
            HeavyStreamError: The stream decodes to more than the budget has
                left, or is damaged after a first filter.
        """
        if self.data is None and self.rawdata is not None:
            self._check()
        super().decode()

    def _check(self) -> None:
        def mend(step: int, data: bytes) -> None:
            # pdfminer's way of reading damaged Flate data takes quadratic
            # time; whole data is read at once. Only a first filter's data
            # is mended: after another filter, the damage is refused.
            if step:
                raise HeavyStreamError
            self.rawdata = zlib.compress(data, 1)
            self.decipher = None

        try:
            data = decoded(self, self._budget.left, mend)
        except OpaqueStreamError:
            return  # Images, which pdfminer never reads.
        if data is None:
            raise HeavyStreamError
        self._budget.left -= len(data)


class _GuardedParser(PDFParser):
    """A parser whose streams decode within a budget."""

    def __init__(self, fp: BinaryIO) -> None:
        super().__init__(fp)
        self._budget = _Budget()

    def push(self, *objs: PSStackEntry[Any]) -> None:
        super().push(
            *(
                (pos, _GuardedStream(obj, self._budget))
                if isinstance(obj, PDFStream)
                else (pos, obj)
                for pos, obj in objs
            )
        )


class GuardedPDF(PDF):
    """A pdfplumber document that decodes within bounds.

    pdfplumber builds its parser itself, so this builds the document as
    pdfplumber does, with a parser of its own.
    """

    def __init__(self, stream: BufferedReader | BytesIO) -> None:
        # pdfplumber's constructor, but for the parser it makes.
        self.stream = stream
        self.stream_is_external = False
        self.path = None
        self.pages_to_parse = None
        self.laparams = None
        self.password = None
        self.unicode_norm = None
        self.raise_unicode_errors = True
        try:
            self.doc = PDFDocument(_GuardedParser(stream))
        except StarGroundError:
            raise
        except Exception as e:
            raise PdfminerException(e) from e
        self.rsrcmgr = PDFResourceManager()
        self.metadata = {}
        for info in self.doc.info:
            self.metadata.update(info)
        for key, value in self.metadata.items():
            try:
                self.metadata[key] = resolve_and_decode(value)
            except Exception as e:
                # A metadata value that can't be read is no reason to fail.
                logger.info("Metadata key %s could not be read: %s", key, e)

    def close(self) -> None:
        """Closes the file, and the pages that were listed.

        Unlike pdfplumber's, this doesn't list them first, which fails the
        same way for a damaged page tree.
        """
        pages = getattr(self, "_pages", ())
        self.flush_cache()
        for page in pages:
            page.close()
        if not self.stream_is_external:
            self.stream.close()


@contextmanager
def open_pdf(source: str | bytes, source_name: str) -> Iterator[GuardedPDF]:
    """Opens a PDF, bounding what its structure decodes to.

    Args:
        source: A path, or the PDF's bytes.
        source_name: The document, for the log.

    Yields:
        The open document, closed afterwards.

    Raises:
        UnreadablePdfError: The file can't be opened as a PDF.
        HeavyStreamError: Its structure decodes to far more than a build
            document's.
    """
    stream: BufferedReader | BytesIO | None = None
    try:
        stream = BytesIO(source) if isinstance(source, bytes) else open(source, "rb")  # noqa: SIM115
        pdf = GuardedPDF(stream)
    except StarGroundError:
        if stream is not None:
            stream.close()
        raise
    except Exception as e:
        if stream is not None:
            stream.close()
        # Whatever is raised here, the file is no PDF that can be opened.
        logger.info("Couldn't open %s as a PDF: %s", source_name, e)
        raise UnreadablePdfError from e
    try:
        yield pdf
    finally:
        pdf.close()


# The boxes pdfplumber reads from a page when the pages are listed: the
# MediaBox always, the others when the page has them.
_BOXES = ("MediaBox", "CropBox", "TrimBox", "BleedBox", "ArtBox")


def _unsized(page: PDFPage) -> bool:
    """Whether pdfplumber can't read a page's size.

    It reads each box as four numbers, and the rotation as a number. A page
    missing its MediaBox, or with a box of three numbers or a rotation that
    is text, fails with a `TypeError` or `IndexError`, not its own error.
    """
    for name in _BOXES:
        if name != "MediaBox" and name not in page.attrs:
            continue
        box = resolve_all(page.attrs.get(name))
        if not (
            isinstance(box, list)
            and len(box) >= 4
            and all(isinstance(v, numbers.Number) for v in box)
        ):
            return True
    rotation = resolve_all(page.attrs.get("Rotate"))
    return rotation is not None and not isinstance(rotation, numbers.Number)


def list_pages(pdf: PDF, source_name: str) -> list[Page]:
    """The PDF's pages, as pdfplumber lists them, if there aren't too many.

    Listing them reads the page tree and each page's size, so a damaged file
    can open and still fail here.

    Raises:
        UnreadablePdfError: The page tree or a page's size is damaged.
        TooManyPagesError: The PDF is far longer than a build document.
    """
    from pdfminer.pdfpage import PDFPage

    try:
        pages = pdf.pages
    except (PdfminerException, MalformedPDFException) as e:
        # pdfminer couldn't walk the page tree, or a box holds a non-number.
        _refuse(e, f"Couldn't list the pages of {source_name}: {e}")
    except (TypeError, IndexError) as e:
        # A box missing or too short, which pdfplumber doesn't check for. The
        # pages up to the failing one are walked again; any other failure is
        # a bug.
        if not any(_unsized(page) for page in PDFPage.create_pages(pdf.doc)):
            raise
        logger.info("A page of %s has no readable size: %s", source_name, e)
        raise UnreadablePdfError from e
    if len(pages) > MAX_PDF_PAGES:
        raise TooManyPagesError(len(pages), MAX_PDF_PAGES)
    return pages


def _refuse(error: Exception, message: str) -> NoReturn:
    """Raises Star Ground's error pdfminer wrapped, else the file is unreadable."""
    cause = error.args[0] if error.args else None
    if isinstance(cause, StarGroundError):
        raise cause from error
    logger.info(message)
    raise UnreadablePdfError from error


def parse_page(page: Page, number: int, source_name: str) -> None:
    """Parses a page's drawing, if it's readable and not far too big.

    pdfplumber parses a page when its contents are first asked for, so
    every reader of a page calls this first.

    Args:
        page: The page, not yet parsed.
        number: The page, counting from 0.
        source_name: The document, for the log.

    Raises:
        HeavyPageError: Its drawing instructions pass `MAX_PAGE_CONTENT_BYTES`.
        UnreadablePdfError: pdfminer can't read them.
        CrowdedPageError: It holds more than `MAX_PAGE_CHARS` characters.
        HeavyStreamError: Its streams decode to more than the document's
            budget.
    """
    if content_bytes(page.page_obj, MAX_PAGE_CONTENT_BYTES) is None:
        raise HeavyPageError(number + 1)
    try:
        chars = len(page.chars)
    except PdfminerException as e:
        _refuse(e, f"Couldn't read page {number + 1} of {source_name}: {e}")
    if chars > MAX_PAGE_CHARS:
        raise CrowdedPageError(number + 1, chars)
