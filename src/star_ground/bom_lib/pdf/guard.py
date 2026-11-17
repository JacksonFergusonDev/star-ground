"""Opens a PDF and parses its pages within bounds, for every reader of one.

A build document is a few hundred kilobytes; a hostile upload can ask a
reader for gigabytes from far less. pdfminer decodes whatever a file says
when it's asked, so each place Star Ground opens a PDF goes through here:
`open_pdf` bounds what the structure decodes to as the file opens,
`list_pages` the page count, and a page's layout (`GuardedPage`) what the
page runs and what pdfminer keeps of it as it's laid out.
"""

import logging
import numbers
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from io import BufferedReader, BytesIO
from typing import Any, BinaryIO, NoReturn

from pdfminer.layout import LTPage
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFGraphicState, PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFStream, resolve_all
from pdfminer.psparser import PSStackEntry
from pdfminer.utils import Matrix, PathSegment, Rect
from pdfplumber.page import Page, PDFPageAggregatorWithMarkedContent
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

logger = logging.getLogger(__name__)

# The most pages a PDF may have. The longest corpus document has 34; a longer
# one isn't a build document, and every page costs seconds of shared CPU.
MAX_PDF_PAGES = 60
# The most characters a page may hold. The sample documents' busiest page
# holds 2,142, and a page of 6pt text about 10,000. Reading time grows with
# the characters (a page of 60,000 takes seconds), so a page past this is
# refused as it's laid out, before the rest of it is read.
MAX_PAGE_CHARS = 20_000
# The most bytes of drawing instructions a page may run: its content streams
# and the forms they draw, a form counted every time it's drawn (see
# `content_bytes`). They compress a thousandfold or more, so this is what
# stops a small upload asking pdfminer to decode gigabytes. Schematics and
# board art drawn as vector paths run the most: of the 1,241 build documents
# in the local corpus, 11 have a page past 1 MiB and the busiest runs
# 3,089,047 bytes (Tonepad Rebote 3 Delay, page 1).
MAX_PAGE_CONTENT_BYTES = 4 << 20
# The most memory a page's layout may take, estimated as pdfminer lays it
# out (`_GuardedDevice`). Memory follows what a page draws, not its bytes: a
# page of drawing instructions at the bound above takes about 90 MiB when
# it's a schematic, and several GiB when it draws only tiny filled
# rectangles or text. So each thing is counted as pdfminer hands it over,
# and the page refused as soon as it passes this, before the rest is kept.
# The corpus's busiest layouts are estimated at 107 MiB (GuitarPCB
# DumbleTone Deluxe, page 2) and 99 MiB (Tonepad Rebote 3 Delay, page 1,
# which tracemalloc measures at about 90 MiB); a schematic drawn the same way
# up to the bound above would take about 170 MiB.
MAX_PAGE_LAYOUT_BYTES = 192 << 20
# What pdfminer and pdfplumber keep of each thing a page draws, measured
# with tracemalloc on `Page.objects`: a character, a form or image, a painted
# shape, and each point of a shape's path. A filled rectangle (five points)
# takes about 4.2 KB, a line 2.9 KB, a curve 1.2 KB a segment.
_CHAR_BYTES = 2_000
_FIGURE_BYTES = 2_000
_SHAPE_BYTES = 2_000
_POINT_BYTES = 500
# What each segment of a path takes while it's drawn, before it's painted.
_SEGMENT_BYTES = 120
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


class _GuardedDevice(PDFPageAggregatorWithMarkedContent):
    """pdfplumber's layout device, refusing a page as soon as it's too big.

    pdfminer hands the device each thing a page draws, and the device keeps
    it, so what the page's layout takes is counted here as it grows rather
    than after the whole page is kept.

    Args:
        rsrcmgr: The document's resources.
        page_number: The page, counting from 1.
        laparams: pdfplumber's layout parameters.
    """

    def __init__(
        self, rsrcmgr: PDFResourceManager, page_number: int, laparams: Any
    ) -> None:
        super().__init__(rsrcmgr, pageno=page_number, laparams=laparams)
        self._page_number = page_number
        self._chars = 0
        self._left = MAX_PAGE_LAYOUT_BYTES
        self._painting = False

    def _keep(self, size: int) -> None:
        self._left -= size
        if self._left < 0:
            raise HeavyPageError(self._page_number)

    def drawing(self, segments: int) -> None:
        """Checks the layout has room for a path of this many segments.

        Raises:
            HeavyPageError: It would pass `MAX_PAGE_LAYOUT_BYTES`.
        """
        if segments * _SEGMENT_BYTES > self._left:
            raise HeavyPageError(self._page_number)

    def render_char(self, *args: Any, **kwargs: Any) -> float:
        """Keeps a character, if the page holds few enough.

        Raises:
            CrowdedPageError: It's one past `MAX_PAGE_CHARS`.
            HeavyPageError: The layout passes `MAX_PAGE_LAYOUT_BYTES`.
        """
        self._chars += 1
        if self._chars > MAX_PAGE_CHARS:
            raise CrowdedPageError(self._page_number, MAX_PAGE_CHARS)
        self._keep(_CHAR_BYTES)
        return super().render_char(*args, **kwargs)

    def begin_figure(self, name: str, bbox: Rect, matrix: Matrix) -> None:
        """Starts a form or image, if the layout has room.

        Raises:
            HeavyPageError: The layout passes `MAX_PAGE_LAYOUT_BYTES`.
        """
        self._keep(_FIGURE_BYTES)
        super().begin_figure(name, bbox, matrix)

    def paint_path(
        self,
        gstate: PDFGraphicState,
        stroke: bool,
        fill: bool,
        evenodd: bool,
        path: Sequence[PathSegment],
    ) -> None:
        """Keeps a painted path's shapes, if the layout has room.

        A path is a shape for each move ('m', and 're' as pdfminer gives
        it), and a point for each segment ('c' has three).

        Raises:
            HeavyPageError: The layout passes `MAX_PAGE_LAYOUT_BYTES`.
        """
        if self._painting:
            # pdfminer paints a path of several shapes one shape at a time.
            super().paint_path(gstate, stroke, fill, evenodd, path)
            return
        shapes = sum(segment[0] == "m" for segment in path)
        points = sum(max(1, (len(segment) - 1) // 2) for segment in path)
        self._keep(shapes * _SHAPE_BYTES + points * _POINT_BYTES)
        self._painting = True
        try:
            super().paint_path(gstate, stroke, fill, evenodd, path)
        finally:
            self._painting = False


class _Path(list[PathSegment]):
    """A path being drawn, refused once the layout has no room for it.

    pdfminer gathers a path's segments until it's painted or dropped ('n'),
    so a page can draw a path of millions of them without painting any.

    Args:
        device: The page's device, whose layout the path takes room from.
    """

    def __init__(self, device: _GuardedDevice) -> None:
        super().__init__()
        self._device = device

    def append(self, segment: PathSegment) -> None:
        """Adds a segment, if the layout has room.

        Raises:
            HeavyPageError: The path would pass `MAX_PAGE_LAYOUT_BYTES`.
        """
        self._device.drawing(len(self) + 1)
        super().append(segment)


class _GuardedInterpreter(PDFPageInterpreter):
    """pdfminer's interpreter, drawing each path as a `_Path`."""

    device: _GuardedDevice

    @property
    def curpath(self) -> list[PathSegment]:
        """The path being drawn."""
        return self._path

    @curpath.setter
    def curpath(self, segments: list[PathSegment]) -> None:
        # pdfminer starts each path with a new list.
        self._path = _Path(self.device)
        self._path.extend(segments)


class GuardedPage(Page):
    """A pdfplumber page laid out within bounds."""

    @property
    def layout(self) -> LTPage:
        """The page's layout, as pdfplumber's, laid out once.

        Raises:
            HeavyPageError: Its drawing instructions pass
                `MAX_PAGE_CONTENT_BYTES`, or its layout
                `MAX_PAGE_LAYOUT_BYTES`.
            CrowdedPageError: It holds more than `MAX_PAGE_CHARS` characters.
            PdfminerException: pdfminer can't read it.
        """
        if not hasattr(self, "_layout"):
            if content_bytes(self.page_obj, MAX_PAGE_CONTENT_BYTES) is None:
                raise HeavyPageError(self.page_number)
            device = _GuardedDevice(
                self.pdf.rsrcmgr, self.page_number, self.pdf.laparams
            )
            try:
                _GuardedInterpreter(self.pdf.rsrcmgr, device).process_page(
                    self.page_obj
                )
            except StarGroundError:
                raise
            except Exception as e:
                raise PdfminerException(e) from e
            self._layout = device.get_result()
        return self._layout


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

    @property
    def pages(self) -> list[Page]:
        """The pages, as pdfplumber lists them, each laid out within bounds."""
        if not hasattr(self, "_pages"):
            self._pages: list[Page] = []
            doctop = 0.0
            for number, page_obj in enumerate(self._page_objs(), start=1):
                page = GuardedPage(self, page_obj, number, initial_doctop=doctop)
                self._pages.append(page)
                doctop += float(page.height)
        return self._pages

    def _page_objs(self) -> Iterator[PDFPage]:
        """The page tree's pages, failing as pdfplumber's listing does."""
        pages = PDFPage.create_pages(self.doc)
        while True:
            try:
                yield next(pages)
            except StopIteration:
                return
            except Exception as e:
                raise PdfminerException(e) from e

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
    """Lays out a page's drawing, if it's readable and not far too big.

    pdfplumber lays out a page when its contents are first asked for, so
    every reader of a page calls this first.

    Args:
        page: The page, as `list_pages` lists it, not yet laid out.
        number: The page, counting from 0.
        source_name: The document, for the log.

    Raises:
        HeavyPageError: Its drawing instructions pass `MAX_PAGE_CONTENT_BYTES`,
            or its layout `MAX_PAGE_LAYOUT_BYTES`.
        UnreadablePdfError: pdfminer can't read them.
        CrowdedPageError: It holds more than `MAX_PAGE_CHARS` characters.
        HeavyStreamError: Its streams decode to more than the document's
            budget.
    """
    try:
        page.layout  # noqa: B018
    except PdfminerException as e:
        _refuse(e, f"Couldn't read page {number + 1} of {source_name}: {e}")
