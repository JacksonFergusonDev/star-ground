"""Opens a build document's PDF, cleans up its pages, and reads them.

Page cleanup removes what would mislead the readers: overprinted text,
skewed glyphs, footnote marks, and another program's OCR layer. The
title is guessed from the first pages.
"""

import logging
import math
import numbers
import re
from dataclasses import replace
from operator import itemgetter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.page import Page
    from pdfplumber.pdf import PDF
from star_ground.bom_lib.errors import (
    CrowdedPageError,
    HeavyPageError,
    TooManyPagesError,
    UnreadablePdfError,
)
from star_ground.bom_lib.locations import locate
from star_ground.bom_lib.pdf.page_checks import (
    footnote_marks,
    marked_labels,
    size_cell,
)
from star_ground.bom_lib.pdf.reading import read_pages
from star_ground.bom_lib.types import (
    BuildVariant,
    Inventory,
    OptionNote,
    ParseResult,
    PDFPageExtraction,
    PDFWord,
    Picture,
    ReadingStats,
)
from star_ground.bom_lib.verification import (
    check_misread_text,
)

# Characters further apart than this start a new word. pdfplumber's 3pt default
# merges words in tightly kerned documents (Muffin Fuzz draws word spaces of
# 1.3pt between overlapping glyphs: 'TONEBYPASSSWITCH').
_WORD_GAP_PT = 1.0
# The most pages a PDF may have. The longest corpus document has 34; a longer
# one isn't a build document, and every page costs seconds of shared CPU.
MAX_PDF_PAGES = 60
# The most characters a page may hold. The sample documents' busiest page
# holds 2,142, and a page of 6pt text about 10,000. Reading time grows with
# the characters (a page of 60,000 takes seconds), so a page past this is
# refused rather than read.
MAX_PAGE_CHARS = 20_000
# The most bytes a page's drawing instructions (its content streams and the
# forms they draw) may decode to. pdfminer decodes and reads them all before
# its characters can be counted, at about 1.8 KB of memory for each one
# drawn, and they compress a thousandfold or more. The sample documents'
# busiest page decodes to 75,622 bytes (LowTide, page 7), and their busiest
# text page draws a character for every 33 bytes, so a page at
# `MAX_PAGE_CHARS` written the same way would take about 660,000.
MAX_PAGE_CONTENT_BYTES = 1 << 20
# Ruled tables are found by pdfplumber, which compares every vertical ruled
# line with every horizontal one, then every crossing of two with every
# other, then every row of cells with every character on the page. A grid of
# 160 by 160 lines took 9.4 s, and the time grows about 15 times with each
# doubling. A page past either bound has its tables read as text, like a page
# without rules: `_table_work` counts the work.
# The busiest sample pages have 16 vertical and 59 horizontal lines (944
# pairs), crossing 261 times, among at most 1,434 characters (a work of
# 442,395). At these bounds a page's tables take at most about a second.
_MAX_RULE_PAIRS = 100_000
_MAX_TABLE_WORK = 10_000_000
_TABLE_SETTINGS = {"text_x_tolerance": _WORD_GAP_PT}

# Initialize Logger
logger = logging.getLogger(__name__)


# Rotation terms this small are a PDF writer's rounding, not a rotation.
_SKEW_EPSILON = 1e-4


def _readable(obj: dict[str, Any]) -> bool:
    """Whether a PDF object is kept for text extraction.

    Characters printed upside down, or running bottom to top, extract in
    reverse: a vector schematic's '10k' reads 'k01' and '100uF' reads 'Fu001',
    which then look like parts lists (Aion FX legacy schematics). Nothing else
    is dropped.
    """
    if obj.get("object_type") != "char":
        return True
    matrix = obj.get("matrix")
    if not matrix:
        return True
    a, b, c, d = matrix[:4]
    # Only exact 180 and 90 degree rotations: text set along an arc (a
    # PedalPCB drill template's 'FOOTSWITCH') passes through every angle.
    upside_down = a < 0 and d < 0 and abs(b) < _SKEW_EPSILON and abs(c) < _SKEW_EPSILON
    # Text running bottom to top extracts reversed too ('Fu001' for '100uF').
    rising = abs(a) < _SKEW_EPSILON and abs(d) < _SKEW_EPSILON and b > 0 and c < 0
    return not (upside_down or rising)


def _page_words(page: Page) -> list[PDFWord]:
    return [
        PDFWord(text=w["text"], x0=w["x0"], x1=w["x1"], top=w["top"], size=w["size"])
        for w in page.extract_words(x_tolerance=_WORD_GAP_PT, extra_attrs=["size"])
    ]


# Two copies of a character this close (points) are one glyph printed twice.
_OVERPRINT_PT = 1.0
# Characters are copies of each other only when all of these match.
_SAME_GLYPH = itemgetter("upright", "text", "fontname", "size")


def _first_copies(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The characters left when copies printed within `_OVERPRINT_PT` go.

    The same characters, in the same order, as pdfplumber's `dedupe_chars`
    with that tolerance, whose last step sorts by `list.index` and so takes
    seconds on a page of thousands of characters (30 s for 30,000). Like it,
    characters of one glyph are clustered by `doctop`, then by `x0`, and each
    cluster keeps its topmost, then leftmost, character.
    """
    from pdfplumber.utils.clustering import cluster_objects

    by_glyph: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for c in chars:
        by_glyph.setdefault(_SAME_GLYPH(c), []).append(c)
    kept: set[int] = set()
    for copies in by_glyph.values():
        for row in cluster_objects(copies, itemgetter("doctop"), _OVERPRINT_PT):
            for cluster in cluster_objects(row, itemgetter("x0"), _OVERPRINT_PT):
                kept.add(id(min(cluster, key=itemgetter("doctop", "x0"))))
    return [c for c in chars if id(c) in kept]


def _without_overprint(page: Page) -> Page:
    """Drops text printed twice a hair apart: board artwork, not the parts list.

    Tonepad draws each document's transfer artwork with its labels printed
    twice, offset by a fraction of a point; extracted, they read doubled
    ('RR77' for R7, 'THRESHLD' beside it). Read once they would be a second,
    conflicting list of the board's labels, so both copies are dropped.
    """
    kept = {id(c) for c in _first_copies(page.chars)}
    doubled = {
        (c["text"], round(c["x0"]), round(c["top"]))
        for c in page.chars
        if id(c) not in kept
    }
    if not doubled:
        return page

    def twin(c: dict[str, Any]) -> bool:
        x, y = round(c["x0"]), round(c["top"])
        return any(
            (c["text"], x + dx, y + dy) in doubled
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        )

    return page.filter(lambda obj: obj.get("object_type") != "char" or not twin(obj))


def _straighten(page: Page) -> Page:
    """Marks characters with negligible rotation as upright, and rounds sizes.

    Some GuitarPCB documents set every character with a rotation of about
    3e-8 and sizes that differ in the 6th decimal; pdfplumber then splits
    every letter into its own word and the parts list comes out letter-spaced
    ('R 1 0  7 5 0 R').
    """
    for char in page.chars:
        matrix = char.get("matrix")
        if (
            not char.get("upright")
            and matrix
            and matrix[0] > 0
            and matrix[3] > 0
            and abs(matrix[1]) < _SKEW_EPSILON
            and abs(matrix[2]) < _SKEW_EPSILON
        ):
            char["upright"] = True
            char["matrix"] = (matrix[0], 0.0, 0.0, *matrix[3:])
        if char.get("upright"):
            # Words split wherever the size differs, and sizes differ by float
            # noise. (Rotated labels keep theirs: joining them makes nonsense.)
            char["size"] = round(char["size"], 2)
    return page


def _without_marks(page: Page) -> Page:
    """The page without superscript footnote markers (see `footnote_marks`).

    Characters are dropped, not words, so ruled tables and page text lose the
    markers too.
    """
    marks = footnote_marks(_page_words(page))
    if not marks:
        return page
    # Each character is compared only with the marks filed in its grid cell,
    # not with every mark: a page of thousands would take minutes. Marks are
    # filed by size, within a factor of two, in every cell of a grid scaled
    # to that size that their box touches.
    grid: dict[float, dict[tuple[int, int], list[PDFWord]]] = {}
    for m in marks:
        bottom = m["top"] + m["size"]
        if not all(math.isfinite(v) for v in (m["x0"], m["x1"], m["top"], bottom)):
            continue
        cell = size_cell(m["size"])
        cells = grid.setdefault(cell, {})
        for x in range(math.floor(m["x0"] / cell), math.floor(m["x1"] / cell) + 1):
            for y in range(math.floor(m["top"] / cell), math.floor(bottom / cell) + 1):
                cells.setdefault((x, y), []).append(m)

    def keep(obj: dict[str, Any]) -> bool:
        if obj.get("object_type") != "char":
            return True
        center = (obj["x0"] + obj["x1"]) / 2
        line = obj["top"] + 0.5
        if not (math.isfinite(center) and math.isfinite(line)):
            return True
        return not any(
            m["x0"] <= center <= m["x1"] and m["top"] <= line <= m["top"] + m["size"]
            for cell, cells in grid.items()
            for m in cells.get((math.floor(center / cell), math.floor(line / cell)), ())
        )

    return page.filter(keep)


def _table_work(page: Page) -> int | None:
    """Roughly how many steps pdfplumber takes to find the page's ruled tables.

    The table finder's edges are merged exactly as it merges them, and their
    crossings counted; the work is the crossings times the crossings and the
    characters (see `_MAX_TABLE_WORK`).

    Returns:
        The work, or None when there are too many pairs of ruled lines even
        to count their crossings.
    """
    from pdfplumber.table import TableSettings, edges_to_intersections, merge_edges
    from pdfplumber.utils.geometry import filter_edges

    settings = TableSettings.resolve(_TABLE_SETTINGS)
    edges = [
        edge
        for orientation in ("v", "h")
        for edge in filter_edges(
            page.edges, orientation, min_length=settings.edge_min_length_prefilter
        )
    ]
    edges = filter_edges(
        merge_edges(
            edges,
            snap_x_tolerance=settings.snap_x_tolerance,
            snap_y_tolerance=settings.snap_y_tolerance,
            join_x_tolerance=settings.join_x_tolerance,
            join_y_tolerance=settings.join_y_tolerance,
        ),
        min_length=settings.edge_min_length,
    )
    vertical = sum(edge["orientation"] == "v" for edge in edges)
    if vertical * (len(edges) - vertical) > _MAX_RULE_PAIRS:
        return None
    crossings = len(
        edges_to_intersections(
            edges,
            settings.intersection_x_tolerance,
            settings.intersection_y_tolerance,
        )
    )
    return crossings * (crossings + len(page.chars))


def _tables(page: Page, number: int) -> list[list[list[str | None]]]:
    """The page's ruled tables, cell by cell; none when they're too much work.

    Args:
        page: The cleaned-up page.
        number: The page, counting from 0, for the log.
    """
    work = _table_work(page)
    if work is None or work > _MAX_TABLE_WORK:
        logger.info("Page %d has too many ruled lines to find tables in", number + 1)
        return []
    return page.extract_tables(_TABLE_SETTINGS)


# An image covering this share of a page may be a parts table.
_PICTURE_SHARE = 0.02


def _pictures(page: Any) -> list[Picture]:
    """A pdfplumber page's large images."""
    area = page.width * page.height
    return [
        Picture(im["x0"], im["top"], im["x1"], im["bottom"])
        for im in page.images
        if (im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) >= _PICTURE_SHARE * area
    ]


# Another program's OCR, drawn as text over a scanned parts list (Five Cats
# Echoes, Funk Machine), confuses digits with letters: a designator 'Rl',
# 'RS', 'Ql' and values 'lOK', 'SOnF', 'lOuF'. Such text looks right on the
# page, so it is read wrong without a trace.
_MISREAD_REF = re.compile(r"(?:R|C|Q|U|D|IC)[lIOSG]{1,2}")
_MISREAD_VALUE = re.compile(
    r"(?=[\dlIOS.]*[lIOS])(?=[\dlIOS.]*\d|[lIOS]{2})[\dlIOS.]+(?:[kKMR]|[pnuµ]F?)"
)
_MISREAD_MIN = 3


def _misread(words: list[PDFWord]) -> bool:
    """Whether a page's text is another program's OCR, full of misreads.

    Both misread designators and misread values must recur: a real document
    can name a part 'RS' or 'DSS', but never prints '10K' as 'lOK'.
    """
    texts = [w["text"] for w in words]
    values = sum(
        bool(_MISREAD_VALUE.fullmatch(t)) and any(ch in "lIOS" for ch in t)
        for t in texts
    )
    refs = sum(bool(_MISREAD_REF.fullmatch(t)) for t in texts)
    return values >= _MISREAD_MIN and refs >= _MISREAD_MIN


# A footer sentence is no title: 'Don't forget to grab a copy of our general
# pedal building guide at: www.five-cats-pedals.co.uk/...' (Five Cats, whose
# only text is the footer).
_MAX_TITLE_WORDS = 8
_URL = re.compile(r"www\.|https?:", re.IGNORECASE)


_DISCLAIMER = re.compile(r"\s*(?:WARNING|DISCLAIMER)\b")
# 'GuitarPCB Presents' above a NostalgiTone title, a bare 'GuitarPCB.com' above V6's,
# and section headings set larger than the title ('PARTS LIST Build Notes:',
# GuitarPCB AfterBlaster).
_PRESENTS = re.compile(
    r"GuitarPCB(?: Presents?|\.com)|(?:PARTS LIST )?Build Notes:?", re.IGNORECASE
)
# 'TIGHT BOX by GuitarPCB', 'GuitarPCB.com - MoRC v4 2020 & v3': the publisher's
# byline is no part of the name.
_BYLINE = re.compile(
    r"\s+by GuitarPCB(?:\.com)?$|^GuitarPCB\.com(?:\s+-)?\s+|^GuitarPCB Presents?\s+"
    # 'Mini-Me Chorus Build Instructions', 'AfterBlaster - Build Instructions'.
    r"|(?:\s+-)?\s+Build Instructions$",
    re.IGNORECASE,
)


# A title has a word to it: '” x ” ca.' is a dimension's scraps (GuitarPCB SWAH v3).
_TITLE_WORD = re.compile(r"[^\W\d_]{3}")
# A glyph the font doesn't map to text: 'NostalgiTone 70s Bri(cid:21)sh Single'.
_UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)")
# 'AND FEATURES', the tail of a heading split across lines: no name starts so.
_FRAGMENT = re.compile(r"(?:and|or)\b|&", re.IGNORECASE)


def _is_title(text: str) -> bool:
    return (
        len(text.split()) <= _MAX_TITLE_WORDS
        and not _URL.search(text)
        and _TITLE_WORD.search(text) is not None
        and not _UNMAPPED_GLYPH.search(text)
        and not _FRAGMENT.match(text)
    )


_TITLE_IGNORE = frozenset(
    {"PEDALPCB", "CONTROLS", "COPYRIGHT", "WWW.PEDALPCB.COM", "LEVEL", "VCC", "GND"}
)


def _title(
    pdf: Any, pages_data: list[PDFPageExtraction], misread: set[int]
) -> str | None:
    """The document's title: the largest words on its title page, if any.

    Args:
        pdf: The open pdfplumber document.
        pages_data: Each page's extracted text, in order.
        misread: Pages whose text layer is misread, so it can't be trusted.

    Returns:
        The title, or None when the title page has none or can't be read.
    """
    # A cover page of warnings comes before the title page (Aion FX
    # legacy Blueshift: 'WARNING / DISCLAIMER').
    first = int(len(pdf.pages) > 1 and bool(_DISCLAIMER.match(pages_data[0].text)))
    if first >= len(pdf.pages) or first in misread:
        return None
    words = _without_overprint(pdf.pages[first]).extract_words(extra_attrs=["size"])
    candidates = [
        w for w in words if w["text"].upper() not in _TITLE_IGNORE and w["size"] > 10
    ]
    while candidates:
        max_size = max(c["size"] for c in candidates)
        title_words = [w for w in candidates if w["size"] >= max_size - 1]
        title_words.sort(key=lambda x: (x["top"], x["x0"]))
        title = " ".join(w["text"] for w in title_words)
        if _PRESENTS.fullmatch(title):
            # 'GuitarPCB Presents' heads the page above the title.
            candidates = [w for w in candidates if w not in title_words]
            continue
        title = _BYLINE.sub("", title)
        return title if _is_title(title) else None
    return None


# The boxes pdfplumber reads from a page when the pages are listed: the
# MediaBox always, the others when the page has them.
_BOXES = ("MediaBox", "CropBox", "TrimBox", "BleedBox", "ArtBox")


def _unsized(page: PDFPage) -> bool:
    """Whether pdfplumber can't read a page's size.

    It reads each box as four numbers, and the rotation as a number. A page
    missing its MediaBox, or with a box of three numbers or a rotation that
    is text, fails with a `TypeError` or `IndexError`, not its own error.
    """
    from pdfminer.pdftypes import resolve_all

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


def _pages(pdf: PDF, source_name: str) -> list[Page]:
    """The PDF's pages, as pdfplumber lists them.

    Listing them reads the page tree and each page's size, so a damaged file
    can open and still fail here.

    Raises:
        UnreadablePdfError: The page tree or a page's size is damaged.
    """
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException

    try:
        return pdf.pages
    except (PdfminerException, MalformedPDFException) as e:
        # pdfminer couldn't walk the page tree, or a box holds a non-number.
        logger.info("Couldn't list the pages of %s: %s", source_name, e)
        raise UnreadablePdfError from e
    except (TypeError, IndexError) as e:
        # A box missing or too short, which pdfplumber doesn't check for. The
        # pages up to the failing one are walked again; any other failure is
        # a bug.
        if not any(_unsized(page) for page in PDFPage.create_pages(pdf.doc)):
            raise
        logger.info("A page of %s has no readable size: %s", source_name, e)
        raise UnreadablePdfError from e


def _parse(page: Page, number: int, source_name: str) -> None:
    """Parses a page's drawing, if it's readable and not far too big.

    pdfplumber parses a page when its contents are first asked for.

    Args:
        page: The page, not yet parsed.
        number: The page, counting from 0.
        source_name: The document, for the log.

    Raises:
        HeavyPageError: Its drawing instructions pass `MAX_PAGE_CONTENT_BYTES`.
        UnreadablePdfError: pdfminer can't read them.
        CrowdedPageError: It holds more than `MAX_PAGE_CHARS` characters.
    """
    from pdfplumber.utils.exceptions import PdfminerException

    from star_ground.bom_lib.pdf.content import content_bytes

    if content_bytes(page.page_obj, MAX_PAGE_CONTENT_BYTES) is None:
        raise HeavyPageError(number + 1)
    try:
        chars = len(page.chars)
    except PdfminerException as e:
        logger.info("Couldn't read page %d of %s: %s", number + 1, source_name, e)
        raise UnreadablePdfError from e
    if chars > MAX_PAGE_CHARS:
        raise CrowdedPageError(number + 1, chars)


def parse_pedalpcb_pdf(filepath: str, source_name: str) -> ParseResult:
    """Parses a PedalPCB Build Document (PDF).

    Uses a multi-stage strategy:
    1. Visual Table Extraction (via pdfplumber lines).
    2. Layout analysis of word positions (column-aware text fallback).
    3. Build planning: variants (the builder must choose) and options (the
       document's first choice is used and the alternative noted).

    Args:
        filepath: Path to the PDF file.
        source_name: Label for the source.

    Returns:
        A ParseResult containing updated inventory, parsing statistics, and extracted title.

    Raises:
        UnreadablePdfError: The file can't be opened as a PDF, or a page is
            damaged.
        TooManyPagesError: The PDF is far longer than a build document.
        HeavyPageError: A page draws far more than a build document's.
        CrowdedPageError: A page holds far more text than a build document's.
    """
    # Lazy import to avoid loading heavy PDF libraries unless needed
    import pdfplumber

    inventory = Inventory()
    stats: ReadingStats = ReadingStats()
    variants: tuple[BuildVariant, ...] = ()
    options: tuple[OptionNote, ...] = ()

    pdf = None
    pages: list[Page] | None = None
    try:
        # Phase 1: File Access
        # We isolate this to distinguish between "Bad File" and "Bad Code"
        try:
            pdf = pdfplumber.open(filepath)
        except Exception as e:
            logger.info("Couldn't open %s as a PDF: %s", source_name, e)
            raise UnreadablePdfError from e
        pages = _pages(pdf, source_name)
        if len(pages) > MAX_PDF_PAGES:
            raise TooManyPagesError(len(pages), MAX_PDF_PAGES)

        # Phase 2: Extraction. Once a page is parsed (`_parse`), a failure
        # here is a bug in Star Ground: the reading's caller records it
        # (`star_ground.bom_lib.errors.record_bug`).
        # Iterate pages once to grab expensive text/table data
        pages_data: list[PDFPageExtraction] = []
        misread: set[int] = set()
        for number, raw_page in enumerate(pages):
            _parse(raw_page, number, source_name)
            straight = _straighten(_without_overprint(raw_page.filter(_readable)))
            marked = marked_labels(_page_words(straight))
            page = _without_marks(straight)
            page_words = _page_words(page)
            if _misread(page_words):
                # Read as an image page instead (see `ocr.py`).
                check_misread_text(stats, number)
                misread.add(number)
                pages_data.append(PDFPageExtraction(tables=[], text="", words=[]))
                continue
            pages_data.append(
                PDFPageExtraction(
                    # Same word gap as the layout reader: kerned cells
                    # otherwise run together ('3mmRedLED,Diffused').
                    tables=_tables(page, number),
                    text=page.extract_text() or "",
                    words=page_words,
                    pictures=_pictures(raw_page),
                    marked=marked,
                )
            )

        # --- PHASE 2: TITLE EXTRACTION ---
        # A heuristic: a page with no title-like words leaves it unset.
        stats.extracted_title = _title(pdf, pages_data, misread)

        inventory, variants, options = read_pages(pages_data, source_name, stats)
        locations = locate(pages_data, inventory)
        variants = tuple(
            replace(v, locations=locate(pages_data, v.inventory)) for v in variants
        )

    finally:
        # Always close the file handle. Closing lists the pages again, so a
        # PDF whose pages couldn't be listed only has its file closed.
        if pdf is not None and pages is not None:
            pdf.close()
        elif pdf is not None:
            pdf.stream.close()

    return ParseResult(
        inventory=inventory,
        stats=stats,
        title=stats.extracted_title,
        variants=variants,
        options=options,
        locations=locations,
    )
