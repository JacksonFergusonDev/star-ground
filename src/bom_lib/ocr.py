"""OCR for parts lists printed as images.

Some build documents print their parts list as a picture (GuitarPCB's DVF and
Blues Power) or as text drawn in outline (SWAH v3): the PDF has no characters
to read. When a parse looks incomplete, the pages that could hold such a list
are rendered and read by OCR, and the recognized words go through the same
readers as a text layer (`parser.read_pages`).

OCR misreads plausibly ('1M' as '10M'), so its parts never join a parts list
on their own. `attempt_ocr` grades the reading, and the caller shows its
verdict and lets the builder accept the parts (`ParseResult.accept_ocr`), each
of which then carries a warning to check it against the document.
"""

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING, Any

from src.bom_lib.designators import BOARD_REF, is_designator, parse_designator
from src.bom_lib.enums import ComponentCategory, OcrVerdict, ParseWarningKind
from src.bom_lib.layout import (
    REF_RANGE,
    find_listings,
    group_lines,
    heads_typed_table,
    page_lines,
    split_cells,
    starts_description,
)
from src.bom_lib.parser import names_builds, read_pages
from src.bom_lib.types import (
    BuildVariant,
    ComponentKey,
    Inventory,
    OcrAttempt,
    ParseResult,
    ParseWarning,
    PDFPageExtraction,
    PDFWord,
    StatsDict,
    create_empty_stats,
)
from src.bom_lib.utils import expand_refs, parse_value_to_decimal

if TYPE_CHECKING:
    from pdfplumber.page import Page
    from PIL.Image import Image

logger = logging.getLogger(__name__)

# Pages are rendered at this resolution; detection then works on a copy whose
# longest side is _DETECT_SIDE pixels. Together they keep one page under about
# 500 MB, with the same accuracy as 300 dpi on GuitarPCB's image tables.
_DPI = 200
# A small page (Five Cats' A5 inserts, 7pt table rows) is rendered larger, to
# at least this many pixels on its longest side: at 200 dpi OCR reads 'R27' as
# 'R21' and '220nF' as '220nl'.
_MIN_RENDER_SIDE = 2400
_DETECT_SIDE = 1280
# At about 2.5 s a page, a document never takes more than about 20 s.
_MAX_PAGES = 8
# A page is a candidate when an image covers this share of it, or when it has
# almost no characters (SWAH v3 draws its table's text as outlines).
_IMAGE_SHARE = 0.02
_FEW_CHARS = 50
# Recognized boxes taller than they are wide (by this factor) are vertical
# labels on PCB renders, never parts-list cells.
_VERTICAL = 1.2
# A box's height includes padding around the glyphs.
_GLYPH_SHARE = 0.75

# A parts table is a grid: at least this many listings start at one x position
# (within _ALIGN_PT). Labels on a PCB render or schematic are scattered, and
# their OCR'd neighbours make junk listings ('V4  IUNMANN').
_MIN_ALIGNED = 4
_ALIGN_PT = 4.0
# Rows with several values side by side that suggest build columns.
_SIDE_BY_SIDE = 2
# Designator numbers in one series: 'C100' starts another.
_SERIES = 100
# Listings that make a designator column for naming unread rows.
_LABEL_COLUMN = 3
# Rows further apart than this many row pitches start another block.
_BLOCK_GAP = 3
# A table's last rows can read as no listing; they are kept when this many
# rows' pitch or less below it.
_TRAILING_ROW = 1.5
# Build-column grids (see `_build_grid`): at least this many value columns,
# each filled in this share of rows; a header cell names the column whose
# span holds its centre.
_GRID_COLUMNS = 2
_GRID_SHARE = 0.5
# Rows a separate block needs to continue the table (see `table_block`).
_CONTINUED_ROWS = 2
# Cells a row above the table must align with the table's (see `_rows_above`).
_ABOVE_CELLS = 3
# Extra character widths set before a designator found inside an OCR box.
_CELL_BREAK = 2
# A table's rows are evenly spaced: this share of its row gaps is within
# _PITCH_TOLERANCE of the typical one. A schematic's aligned labels are not.
_MIN_EVEN_ROWS = 0.7
_PITCH_TOLERANCE = 0.25

# Verdict thresholds, set on the GuitarPCB tuning documents.
_MIN_PARTS = 5
_MAX_SUSPECT_SHARE = 0.15
_MIN_COVERAGE = 0.6
_MAX_GAP_SHARE = 0.25
# A reading naming more designators it did not read than this share of those
# it did is incomplete.
_MAX_UNREAD_SHARE = 0.1

# One OCR job at a time: each holds a rendered page and the model's buffers.
_LOCK = threading.Lock()

# Standard resistor and capacitor values (the E24 series), per decade.
_E24 = frozenset(
    Decimal(m) / 10
    for m in (
        *(10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30),
        *(33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91),
    )
)
_INJECTED = " (Inj)"
_MAX_VALUE_WORDS = 5
# Characters printed in parts-list values: Latin letters, digits, punctuation,
# and the micro and ohm signs.
# What may follow a passive's or pot's value in a parts list: a rating, a
# dielectric, a taper.
_RATING = r"\d+(?:\.\d+)?V|\d+(?:\.\d+)?%|\(?(?i:mlcc|film|box|ceramic|electrolytic|tantalum|np|bp|bipolar|metal|carbon|trim(?:mer|pot)?)\)?"
_QUALIFIERS = {
    ComponentCategory.RESISTORS: re.compile(_RATING),
    ComponentCategory.CAPACITORS: re.compile(_RATING),
    ComponentCategory.POTENTIOMETERS: re.compile(
        r"\(?(?i:lin(?:ear)?|log|audio|rev(?:erse)?|anti-?log|dual|trim(?:mer|pot)?|"
        r"16mm|9mm|right-?angle)\)?"
    ),
}
_VOLTS = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)
_PROSE_WORD = re.compile(r"[a-z]{2,}[.,;:]?")
_VALUE_CHARS = re.compile(r"[\x20-\x7e\u00b5\u03a9\u2126]*")
# A part label in a designator column: 'CLIP1', 'GAIN-TOG1', 'RELAX/PUSH',
# '3PDT1'.
_LABEL = re.compile(r"(?=\d*[A-Z])[A-Z0-9][A-Za-z0-9_/-]{0,15}")
# Column headings and section titles of a parts table.
_HEADERS = frozenset(
    {
        "REF",
        "REFERENCE",
        "LOCATION",
        "PART",
        "VALUE",
        "TYPE",
        "FOOTPRINT",
        "RESISTORS",
        "CAPACITORS",
        "DIODES",
        "TRANSISTORS",
        "ICS",
        "POTENTIOMETERS",
        "SWITCHES",
        "COMPONENT",
        "TOTAL",
        "UNIQUE",
    }
)
_NAME_ON_VALUE = re.compile(r"\b([A-Z]{3,}\d{1,2})([ABCW]\d+(?:\.\d+)?[KkM])\b")
_SERIAL_REF = re.compile(r"([A-Z]{1,3})(\d{1,3})")
_DASHES = "-\u2013\u2014"
# 'TR1 -TR3' read as one box: the range's spaces would split it in two.
_SPACED_RANGE = re.compile(
    r"\b([A-Z]{1,3}\d{1,3}) ?([-\u2013\u2014]) ?(?=[A-Z]{0,3}\d)"
)


@dataclass(frozen=True)
class OcrBox:
    """One recognized run of text, in page coordinates (points).

    Attributes:
        text: The recognized text.
        x0: Left edge.
        x1: Right edge.
        top: Top edge.
        bottom: Bottom edge.
    """

    text: str
    x0: float
    x1: float
    top: float
    bottom: float


Recognizer = Callable[["Image", float], list[OcrBox]]


@cache
def _engine() -> Any:
    from rapidocr import RapidOCR

    return RapidOCR(
        params={"Det.limit_side_len": _DETECT_SIDE, "Det.limit_type": "max"}
    )


def recognize(image: Image, scale: float) -> list[OcrBox]:
    """Reads the text on a page image with RapidOCR.

    Args:
        image: The rendered page.
        scale: Points per pixel of the rendering.

    Returns:
        The recognized boxes, in points.
    """
    out = _engine()(image)
    boxes = []
    for corners, text in zip(
        out.boxes if out.boxes is not None else (), out.txts or (), strict=True
    ):
        xs = [float(p[0]) * scale for p in corners]
        ys = [float(p[1]) * scale for p in corners]
        boxes.append(OcrBox(str(text), min(xs), max(xs), min(ys), max(ys)))
    return boxes


def needs_ocr(result: ParseResult) -> bool:
    """Whether a parse looks incomplete in a way OCR might fix.

    Either no parts list was read at all, the document names designators
    that are missing from the parts list, or a page's text was set aside as
    another program's misread OCR. A document with several builds was
    read well enough to find them, and is left alone.

    Args:
        result: The text-layer parse of the document.

    Returns:
        True when OCR should be attempted.
    """
    if result.requires_choice:
        return False
    return any(
        w.kind
        in (
            ParseWarningKind.NO_PARTS_LIST,
            ParseWarningKind.UNEXTRACTED_REF,
            ParseWarningKind.MISREAD_TEXT,
        )
        for w in result.stats["warnings"]
    )


def _candidate(page: Page) -> bool:
    area = page.width * page.height
    pictured = any(
        (im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) >= _IMAGE_SHARE * area
        for im in page.images
    )
    return pictured or len(page.chars) < _FEW_CHARS


def _join_ranges(row: list[OcrBox]) -> list[OcrBox]:
    """Rejoins a designator range OCR read as separate boxes.

    'TR1 <en dash> TR3' (Myton Blonde) comes back as 'TR1', '<en dash>TR3', or
    'TR1', '<en dash>', 'TR3'; split across cells, the range would be lost.
    """
    joined: list[OcrBox] = []
    for box in row:
        text = box.text.strip()
        prev = joined[-1].text if joined else ""
        if joined and (
            (text[:1] in _DASHES and _SERIAL_REF.fullmatch(prev))
            or (prev[-1:] in _DASHES and _SERIAL_REF.fullmatch(text))
        ):
            last = joined[-1]
            joined[-1] = OcrBox(prev + text, last.x0, box.x1, last.top, last.bottom)
        else:
            joined.append(box)
    return joined


def _starts_cell(token: str) -> bool:
    return (
        BOARD_REF.fullmatch(token) is not None or REF_RANGE.fullmatch(token) is not None
    )


def page_words(boxes: list[OcrBox]) -> PDFPageExtraction:
    """Turns recognized boxes into the positioned words the readers expect.

    A box can hold several words; each gets its share of the box's width. Boxes
    on one row are given one top, as text on a line shares a baseline: a cell
    read 2pt higher than its neighbours would otherwise start a line of its own.

    Args:
        boxes: Recognized boxes on one page.

    Returns:
        The page's text and words (OCR finds no ruled tables).
    """
    flat = [
        b
        for b in boxes
        if b.text.strip()
        and not (len(b.text) > 2 and b.bottom - b.top > _VERTICAL * (b.x1 - b.x0))
    ]
    flat.sort(key=lambda b: (b.top + b.bottom) / 2)
    rows: list[list[OcrBox]] = []
    for box in flat:
        height = box.bottom - box.top
        if rows:
            # Against the row as a whole and the smaller height: a tall box
            # (a wrapped cell) must not chain rows a few points apart into one
            # (Five Cats tables have 7pt rows).
            row = rows[-1]
            middle = median((b.top + b.bottom) / 2 for b in row)
            row_height = median(b.bottom - b.top for b in row)
            gap = abs((box.top + box.bottom) / 2 - middle)
            # Boxes one above the other are two rows however close they are
            # ('THROB | B25K' over 'LEVEL | A100K', Five Cats Deep Down).
            stacked = any(
                min(b.x1, box.x1) - max(b.x0, box.x0)
                > 0.5 * min(b.x1 - b.x0, box.x1 - box.x0)
                for b in row
            )
            if gap < 0.5 * min(height, row_height) and not stacked:
                row.append(box)
                continue
        rows.append([box])

    words: list[PDFWord] = []
    lines = []
    for row in rows:
        top = median(b.top for b in row)
        size = round(median(b.bottom - b.top for b in row) * _GLYPH_SHARE, 1)
        row.sort(key=lambda b: b.x0)
        for box in _join_ranges(row):
            # OCR reads the micro sign as Greek mu ('1μ'); values use 'µ'.
            text = _SPACED_RANGE.sub(r"\1\2", box.text.replace("μ", "µ"))
            # A name printed against its value: 'DISTORTION1A100K' (Rat King).
            text = _NAME_ON_VALUE.sub(r"\1 \2", text)
            tokens = text.split()
            width = (box.x1 - box.x0) / len(" ".join(tokens))
            pos = 0.0
            for k, token in enumerate(tokens):
                x0 = box.x0 + pos * width
                if (
                    words
                    and words[-1]["top"] == top
                    and (
                        _starts_cell(token)
                        # A value's description starts a cell: 'TL072 | Dual
                        # Op Amp', in one box or two.
                        or starts_description(words[-1]["text"], " ".join(tokens[k:]))
                    )
                ):
                    # A designator starts a cell, however close OCR set it to
                    # the cell before ('16V  D1-D2 1N4148', SWAH), even
                    # within one box.
                    x0 = max(x0, words[-1]["x1"] + _CELL_BREAK * width)
                words.append(
                    PDFWord(
                        text=token,
                        x0=x0,
                        x1=x0 + len(token) * width,
                        top=top,
                        size=size,
                    )
                )
                pos = (x0 - box.x0) / width + len(token) + 1
        lines.append(" ".join(b.text for b in row))
    return {"tables": [], "text": "\n".join(lines), "words": words}


def _clusters(values: list[float], tolerance: float) -> list[list[float]]:
    """Groups sorted values whose neighbours are within a tolerance."""
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _even(gaps: list[float], pitch: float) -> bool:
    """Whether most gaps are whole multiples of a pitch."""
    even = sum(
        1 <= round(gap / pitch) <= _BLOCK_GAP
        and abs(gap - round(gap / pitch) * pitch) <= _PITCH_TOLERANCE * pitch
        for gap in gaps
    )
    return even >= _MIN_EVEN_ROWS * len(gaps)


def _pitch(rows: list[float]) -> float | None:
    """The row pitch of evenly spaced rows (blank rows allowed), or None.

    The median gap is the pitch unless most rows sit a blank row apart:
    M-Drive's rows alternate gaps of one and two pitches, so the median is two
    and the rows one pitch apart look irregular. Half the median is tried
    then.
    """
    gaps = [b - a for a, b in pairwise(rows)]
    if len(gaps) < 2:
        return None
    pitch = median(gaps)
    return next((p for p in (pitch, pitch / 2) if _even(gaps, p)), None)


def _regular(rows: list[float]) -> bool:
    """Whether rows are evenly spaced, as a table's are (blank rows allowed)."""
    return _pitch(rows) is not None


def table_block(data: PDFPageExtraction) -> PDFPageExtraction:
    """Keeps only the words of the page's parts table.

    The table is the largest block of evenly spaced rows whose listings start
    in aligned columns. A page with no such block (a PCB render, a schematic,
    prose) keeps nothing.

    Args:
        data: One OCR'd page.

    Returns:
        The page restricted to its parts table, possibly empty.
    """
    words = data["words"]
    lines = group_lines(words)
    listings = find_listings(words)
    columns = [
        c
        for c in _clusters([lst.x0 for lst in listings], _ALIGN_PT)
        if len(c) >= _MIN_ALIGNED
    ]
    aligned = [lst for lst in listings if any(c[0] <= lst.x0 <= c[-1] for c in columns)]
    if not aligned:
        return {"tables": [], "text": "", "words": []}
    tops = sorted({lines[lst.line][0]["top"] for lst in aligned})
    pitch = median(b - a for a, b in pairwise(tops)) if len(tops) > 1 else 0.0
    # A row that read as no listing leaves a gap of one more pitch.
    blocks = _clusters(tops, (_BLOCK_GAP + 1 + _PITCH_TOLERANCE) * pitch)
    rows = max(blocks, key=len)
    row_pitch = _pitch(rows)
    if row_pitch is None:
        # Aligned labels on a schematic (Apollo Tremolo's) are no table.
        return {"tables": [], "text": "", "words": []}
    pitch = min(pitch, row_pitch)
    # Rows that read as no listing can split one table in two ('LED | LED'
    # rows, Tone Monarch's daughterboard): a later block in the same
    # designator columns, at the same pitch, continues it.
    starts_at = {lines[lst.line][0]["top"]: lst.x0 for lst in aligned}
    main_columns = [
        c for c in columns if any(c[0] <= starts_at[top] <= c[-1] for top in rows)
    ]
    # So can a block above it: Rat King prints two builds' tables, one over
    # the other, in the same columns.
    main = rows
    for block in blocks:
        if (
            block is not main
            and len(block) >= _CONTINUED_ROWS
            and _regular([*block, block[-1] + pitch])
            and all(
                any(c[0] <= starts_at[top] <= c[-1] for c in main_columns)
                for top in block
            )
        ):
            rows = sorted(rows + block)
    # A row that read as no listing ('SUBS | SDPT Toggle', WoollyTron) still
    # belongs to the table when it continues the table's rows from one of
    # its designator columns, so the reading can say it was not read.
    # Only below the table and at its pitch: above it lies the board drawing,
    # whose labels can line up with the columns.
    left = min(lst.x0 for lst in aligned if lines[lst.line][0]["top"] in rows)
    starts = sorted(
        (line[0]["top"], line[0]["x0"])
        for line in lines
        if any(c[0] - _ALIGN_PT <= line[0]["x0"] <= c[-1] + _ALIGN_PT for c in columns)
    )
    for top, x0 in starts:
        if 0 < top - rows[-1] <= _TRAILING_ROW * pitch:
            rows.append(top)
            left = min(left, x0)
    listed = {lines[lst.line][0]["top"] for lst in listings}
    rows = _rows_above(lines, rows, pitch, listed)
    # The table's own typed header row, just above it (Rat King's first build).
    # An untyped grid's ('Part | Value', GuitarPCB) reads better without it.
    header = [
        line
        for line in lines
        if 0 < rows[0] - line[0]["top"] <= _TRAILING_ROW * pitch
        and heads_typed_table(line)
    ][-1:]
    for line in header:
        rows = [line[0]["top"], *rows]
        # The whole header: 'Part' can start a little left of 'R1' (GuitarPCB).
        left = min(left, line[0]["x0"])
    margin = pitch / 2
    kept = [
        w
        for w in words
        if rows[0] - margin <= w["top"] <= rows[-1] + margin and w["x0"] >= left - 1
    ]
    text = "\n".join(" ".join(w["text"] for w in line) for line in group_lines(kept))
    grid = _build_grid(lines, rows, pitch)
    # Words left of the table on its rows name parts of it ('Standard RAT'
    # beside one build's rows, Five Cats Rat King).
    labels = [
        w
        for w in words
        if rows[0] - margin <= w["top"] <= rows[-1] + margin and w["x1"] < left - 1
    ]
    return {
        "tables": [grid] if grid else [],
        "text": text,
        "words": kept,
        "labels": labels,
    }


def _build_grid(
    lines: list[list[PDFWord]], rows: list[float], pitch: float
) -> list[list[str | None]] | None:
    """A table of build columns, rebuilt as the ruled table OCR cannot see.

    OKS Fuzz prints 'Component | ColorSound | 1996 | Meathead | Dark Meathead |
    Ritual' over rows of a designator and its value in each build. Read as
    listings, the values run together ('2M 2M 2M 2M 2M'); as a table, the
    readers give each column its build (`parser._value_column_grid`).

    Returns:
        The header and rows, or None when the table has no named value columns.
    """
    tops = set(rows)
    body = [split_cells(line) for line in lines if line[0]["top"] in tops]
    starts = [
        cell.x0
        for cells in body
        if len(cells) >= _GRID_COLUMNS + 1
        for cell in cells[1:]
    ]
    columns = [
        c for c in _clusters(starts, _ALIGN_PT) if len(c) >= _GRID_SHARE * len(body)
    ]
    if len(columns) < _GRID_COLUMNS:
        return None
    lefts = [c[0] - _ALIGN_PT for c in columns]

    def column(x: float) -> int | None:
        """The column whose span (from its left edge to the next's) holds x."""
        inside = [k for k, left in enumerate(lefts) if x >= left]
        return inside[-1] if inside else None

    above = [
        line
        for line in lines
        if 0 < rows[0] - line[0]["top"] <= (_BLOCK_GAP + 1) * pitch
    ]
    for line in sorted(above, key=lambda line: -line[0]["top"]):
        cells = split_cells(line)
        names: list[list[str]] = [[] for _ in columns]
        for cell in cells[1:]:
            # Header cells are centred over their column.
            k = column((cell.x0 + cell.words[-1]["x1"]) / 2)
            if k is not None:
                names[k].append(cell.text)
        if cells[0].x0 >= lefts[0] or sum(bool(n) for n in names) < len(columns):
            continue
        header = [cells[0].text, *(" ".join(n) for n in names)]
        if not names_builds(header):
            continue
        table: list[list[str | None]] = [list(header)]
        for row in body:
            values: list[str | None] = [None] * len(columns)
            for cell in row[1:]:
                k = column(cell.x0)
                if k is not None:
                    values[k] = cell.text
            table.append([row[0].text, *values])
        return table
    return None


def _rows_above(
    lines: list[list[PDFWord]], rows: list[float], pitch: float, listed: set[float]
) -> list[float]:
    """Adds the listings above a table that continue its cell columns.

    A table beside the main one can start higher (Five Cats Samurai Boost:
    'Trim1 | 10K | 6mm Trim Pot' above 'R1 | 1M'). The board drawing above a
    table rarely lines up with it, so a row must hold a listing, start cells
    at `_ABOVE_CELLS` of the table's cell columns, and lie within a table gap
    of the next. Header rows are left out: a build-column header ('Jack |
    Kraken | Sheriff', Victory III) is read better without them.
    """
    tops = set(rows)
    edges = [
        cell.x0
        for line in lines
        if line[0]["top"] in tops
        for cell in split_cells(line)
    ]
    above: list[float] = []
    for line in sorted(
        (line for line in lines if line[0]["top"] < rows[0]),
        key=lambda line: -line[0]["top"],
    ):
        top = line[0]["top"]
        if (above or rows)[-1 if above else 0] - top > (
            _BLOCK_GAP + 1 + _PITCH_TOLERANCE
        ) * pitch:
            break
        aligned = sum(
            any(abs(cell.x0 - x) <= _ALIGN_PT for x in edges)
            for cell in split_cells(line)
        )
        if aligned >= _ABOVE_CELLS:
            above.append(top)
    # Rows between listings belong too ('Boost | Toggle | DPDT', which reads
    # as no listing); those above the highest listing do not.
    while above and above[-1] not in listed:
        above.pop()
    return [*reversed(above), *rows]


def _base_ref(ref: str) -> str:
    return ref.removesuffix(_INJECTED)


def _refs(inventory: Inventory) -> set[str]:
    return {_base_ref(r) for data in inventory.values() for r in data["refs"]}


def _only_refs(inventory: Inventory, keep: set[str]) -> Inventory:
    """The inventory restricted to some designators (and parts injected for them)."""
    kept = Inventory()
    for key, data in inventory.items():
        refs = [r for r in data["refs"] if _base_ref(r) in keep]
        if not refs:
            continue
        per_ref = data["qty"] // len(data["refs"]) or 1
        for source, source_refs in data["sources"].items():
            for ref in source_refs:
                if _base_ref(ref) in keep:
                    kept.add_part(source, key, ref, per_ref)
    return kept


def _union(variants: tuple[BuildVariant, ...]) -> Inventory:
    """Every part of every build, each designator and value once."""
    union = Inventory()
    for variant in variants:
        for key, data in variant.inventory.items():
            for source, refs in data["sources"].items():
                for ref in refs:
                    if key not in union or ref not in union[key]["refs"]:
                        union.add_part(source, key, ref, 1)
    return union


def _listed(inventory: Inventory) -> dict[str, ComponentKey]:
    return {
        ref: key
        for key, data in inventory.items()
        for ref in data["refs"]
        if not ref.endswith(_INJECTED)
    }


def _plausible(key: ComponentKey) -> bool:
    """Whether a value read by OCR looks like a real part's value.

    Resistors and capacitors must have a standard (E24) value: misreads such
    as '05' or '4' for '4k7' rarely do. Other parts need a value of a few
    characters. No value holds another designator ('SPEED C50k 10u C5' is a
    schematic's labels run together) or characters no parts list uses ('ε3').
    """
    value = key.value.strip()
    if not _VALUE_CHARS.fullmatch(value) or any(
        _SERIAL_REF.fullmatch(word) and is_designator(word) for word in value.split()
    ):
        return False
    words = value.split() or [""]
    if sum(_PROSE_WORD.fullmatch(word) is not None for word in words) >= 2:
        # A sentence beside the table ran in: 'J201/2N5457 super small size.'
        return False
    if key.category == ComponentCategory.POTENTIOMETERS:
        # A pot's control name may come first: 'P1  GAIN 1M Log'.
        while len(words) > 1 and words[0].isalpha() and words[0].isupper():
            words = words[1:]
    head, *rest = words
    if key.category in _QUALIFIERS and not all(
        _QUALIFIERS[key.category].fullmatch(word) for word in rest
    ):
        # Another column's text ran into the value: '470n  Tone B50k'.
        return False
    if key.category in (ComponentCategory.RESISTORS, ComponentCategory.CAPACITORS):
        number = parse_value_to_decimal(head)
        if number is None or number <= 0:
            return False
        mantissa = number.scaleb(-number.adjusted()).quantize(Decimal("0.1"))
        return mantissa in _E24
    return len(value) >= 2


def _gap_share(refs: set[str]) -> float:
    """Share of numbers missing from each designator series ('R1-R31' without R7).

    A complete parts list numbers its parts without gaps; a list read in part
    leaves holes.
    """
    numbers: dict[str, set[int]] = {}
    for ref in refs:
        m = _SERIAL_REF.fullmatch(ref)
        if m:
            numbers.setdefault(m.group(1), set()).add(int(m.group(2)))
    expected = sum(max(ns) for ns in numbers.values() if len(ns) >= 3)
    present = sum(len(ns) for ns in numbers.values() if len(ns) >= 3)
    return 1 - present / expected if expected else 0.0


def _unlisted(pages_data: list[PDFPageExtraction], listed: set[str]) -> set[str]:
    """Labels in a table's designator columns that were read as no part.

    A row whose value the readers reject ('CLIP1 | SW_SPDT', 'R10A1 |
    JUMPER', 'PWR1 | Barrel_Jack') would otherwise vanish without a trace.

    Args:
        pages_data: The table blocks OCR read.
        listed: Every designator the reading (or the text layer) listed.

    Returns:
        The labels to check, as printed.
    """
    unlisted = set()
    for data in pages_data:
        listings = find_listings(data["words"])
        # A short column of controls counts ('Trim1 | Trim2 | VOL', above
        # Samurai Boost's unread 'Boost | Toggle | DPDT').
        columns = [
            c
            for c in _clusters([lst.x0 for lst in listings], _ALIGN_PT)
            if len(c) >= _LABEL_COLUMN
        ]
        for cells in page_lines(data["words"]):
            for cell in cells:
                label = cell.text.split()[0]
                refs = expand_refs(label) if REF_RANGE.fullmatch(label) else [label]
                if (
                    any(
                        c[0] - _ALIGN_PT <= cell.x0 <= c[-1] + _ALIGN_PT
                        for c in columns
                    )
                    and _LABEL.fullmatch(label)
                    and not {r.upper() for r in refs} <= listed
                    and label.upper() not in _HEADERS
                ):
                    unlisted.add(label)
    return unlisted


def _unread(listed: dict[str, ComponentKey], mentioned: set[str]) -> set[str]:
    """Designators a reading seems to have missed.

    OCR can skip a cell. Within a series it has read ('R1' ... 'R31'), a
    missing number is suspicious, as is a designator printed elsewhere in the
    document (a PCB render's 'IC1'), unless the parts list names that kind of
    part instead (DVF's pots are 'GAIN', 'LEVEL'..., its render's 'P1'-'P5').

    Args:
        listed: Every designator in the parts list, with its part.
        mentioned: Every word OCR read on the document's image pages, as printed.

    Returns:
        The designators to check.
    """
    numbers: dict[str, set[int]] = {}
    for ref in listed:
        m = _SERIAL_REF.fullmatch(ref)
        if m:
            numbers.setdefault(m.group(1), set()).add(int(m.group(2)))
    # Each hundred is a series of its own: Five Cats numbers the power
    # section 'C100', 'D100' after 'C1'-'C13'.
    series = {
        (prefix, n // _SERIES): {k for k in ns if k // _SERIES == n // _SERIES}
        for prefix, ns in numbers.items()
        for n in ns
    }
    unread = {
        f"{prefix}{n}"
        for (prefix, hundred), ns in series.items()
        if len(numbers[prefix]) >= 3
        for n in range(max(1, min(ns) if hundred else 1), max(ns))
        if n not in ns
    }
    values = {word for key in listed.values() for word in key.value.split()}
    named = {
        key.category for ref, key in listed.items() if not _SERIAL_REF.fullmatch(ref)
    }
    for word in mentioned - values:
        m = _SERIAL_REF.fullmatch(word)
        designator = parse_designator(word) if m else None
        if m is None or designator is None or designator.category is None:
            continue  # pads ('S4'), versions ('v4'), junk read off traces
        prefix, n = m.group(1), int(m.group(2))
        if prefix in numbers:
            # The enclosure badge's 'C60' is no capacitor beside 'C1'-'C13'.
            if (prefix, n // _SERIES) in series and n <= max(
                series[prefix, n // _SERIES]
            ):
                unread.add(word)
        elif designator.category not in named:
            unread.add(word)
    return unread - set(listed)


def _several_values(value: str) -> bool:
    """Whether a value is several resistor or capacitor values ('68K 43K 47K')."""
    # A voltage rating is no second value: '22n 63V' (GuitarPCB SWAH).
    words = [w for w in value.split() if not _VOLTS.fullmatch(w)]
    return len(words) > 1 and all(parse_value_to_decimal(w) is not None for w in words)


def _verdict(
    parts: int,
    suspect: int,
    coverage: float | None,
    gaps: float,
    conflicts: bool = False,
) -> OcrVerdict:
    if parts < _MIN_PARTS:
        return OcrVerdict.UNUSABLE
    if (
        conflicts
        or suspect > _MAX_SUSPECT_SHARE * parts
        or (coverage is not None and coverage < _MIN_COVERAGE)
        or gaps > _MAX_GAP_SHARE
    ):
        return OcrVerdict.PARTIAL
    return OcrVerdict.COMPLETE


def _ocr_warning(ref: str, key: ComponentKey, plausible: bool) -> ParseWarning:
    value = key.value or "no value"
    if plausible:
        message = (
            f"{ref} ({value}) was read from an image by OCR. Check it against "
            "the document."
        )
    else:
        message = (
            f"{ref} ({value}) was read from an image by OCR and looks wrong. "
            "Check it against the document."
        )
    return ParseWarning(ParseWarningKind.OCR_READ, ref, message)


def attempt_ocr(
    filepath: str,
    result: ParseResult,
    source_name: str,
    recognizer: Recognizer = recognize,
) -> OcrAttempt | None:
    """Reads a document's image pages with OCR and grades the reading.

    Args:
        filepath: Path to the PDF.
        result: The text-layer parse of the same document.
        source_name: Label for the source.
        recognizer: The OCR engine (tests substitute a fake).

    Returns:
        The attempt, or None when OCR was not needed, or read too little to
        add to a document whose text layer did list parts.
    """
    if not needs_ocr(result):
        return None
    import pdfplumber

    pages_data: list[PDFPageExtraction] = []
    mentioned: set[str] = set()
    read: list[int] = []
    with _LOCK, pdfplumber.open(filepath) as pdf:
        for number, page in enumerate(pdf.pages):
            if len(read) == _MAX_PAGES:
                break
            if not _candidate(page):
                continue
            dpi = max(_DPI, 72 * _MIN_RENDER_SIDE / max(page.width, page.height))
            image = page.to_image(resolution=dpi).original.convert("RGB")
            words = page_words(recognizer(image, 72 / dpi))
            mentioned |= {w["text"] for w in words["words"]}
            pages_data.append(table_block(words))
            read.append(number)
            page.close()

    stats: StatsDict = create_empty_stats()
    inventory = Inventory()
    variants: tuple[BuildVariant, ...] = ()
    if pages_data:
        try:
            inventory, variants, _ = read_pages(
                pages_data, source_name, stats, tables_only=True
            )
        except Exception:
            logger.exception("Reading OCR text failed for %s", source_name)
    if variants:
        # Several builds (Five Cats Octavia's 'To build the vintage Octavia'
        # table, OKS Fuzz's version columns): every part of every build.
        inventory = _union(variants)

    known = _refs(result.inventory)
    # A sentence beside a designator is prose OCR read near the table, not a
    # value: 'V4  2020 version seen above (W x H) ...' (DVF).
    found = {
        ref
        for ref, key in _listed(inventory).items()
        if len(key.value.split()) <= _MAX_VALUE_WORDS
    } - known
    if not found and known:
        return None
    parts = _only_refs(inventory, found)
    builds = tuple(
        BuildVariant(v.name, kept, sum(d["qty"] for d in kept.values()))
        for v in variants
        if (kept := _only_refs(v.inventory, found))
    )

    missing = {
        w.ref
        for w in result.stats["warnings"]
        if w.kind == ParseWarningKind.UNEXTRACTED_REF
    }
    coverage = len(missing & found) / len(missing) if missing else None
    warnings = []
    suspect = 0
    for key, data in parts.items():
        plausible = _plausible(key)
        for ref in data["refs"]:
            if ref.endswith(_INJECTED):
                continue
            suspect += not plausible
            warnings.append(_ocr_warning(ref, key, plausible))
    warnings += [
        ParseWarning(
            ParseWarningKind.UNEXTRACTED_REF,
            ref,
            f"{ref} may be missing: OCR did not read it from the parts list. Check "
            "the document.",
        )
        for ref in sorted(
            # A part that looks misread ('C60 | MM', an enclosure badge) does
            # not extend a series.
            _unread(
                _listed(result.inventory)
                | {r: k for r, k in _listed(parts).items() if _plausible(k)},
                mentioned,
            )
            # A row read but dropped (its value too long to be one) is named.
            | _unlisted(pages_data, {r.upper() for r in {*_listed(parts), *known}})
        )
    ]
    # The OCR reading's own self-checks: a designator read twice with two
    # values means the image holds several builds side by side (Big Muff X6)
    # or a misread, and either way the reading cannot be trusted as one list.
    own = [
        w
        for w in stats["warnings"]
        if w.kind != ParseWarningKind.NO_PARTS_LIST and w.ref not in known
    ]
    seen = {(w.kind, w.ref) for w in warnings}
    warnings += [w for w in own if (w.kind, w.ref) not in seen]
    conflicts = {w.ref for w in own if w.kind == ParseWarningKind.CONFLICTING_DUPLICATE}
    side_by_side = sorted(
        ref
        for key, data in parts.items()
        if _several_values(key.value)
        for ref in data["refs"]
    )
    if len(side_by_side) >= _SIDE_BY_SIDE:
        # Build columns the reading did not recognize ('R11 | 68K | 43K |
        # 47K'): read as one list, the builds run together.
        conflicts.add("")
        warnings.append(
            ParseWarning(
                ParseWarningKind.CONFLICTING_DUPLICATE,
                "",
                f"Some rows list several values side by side ({', '.join(side_by_side[:3])}"
                f"{'...' if len(side_by_side) > 3 else ''}): the parts list may "
                "describe several builds. Check the document.",
            )
        )
    unread = sum(w.kind == ParseWarningKind.UNEXTRACTED_REF for w in warnings)
    verdict = _verdict(
        len(found), suspect, coverage, _gap_share(found | known), bool(conflicts)
    )
    if verdict == OcrVerdict.COMPLETE and unread > _MAX_UNREAD_SHARE * len(found):
        # Designators the reading names but did not read (K-Buffer: 5 of 12).
        verdict = OcrVerdict.PARTIAL
    warnings.sort(key=lambda w: w.ref)
    if known and verdict == OcrVerdict.UNUSABLE:
        # The text layer read a parts list; a few stray OCR words are no
        # evidence of an image one.
        return None
    return OcrAttempt(
        verdict=verdict,
        inventory=Inventory() if builds else parts,
        warnings=tuple(warnings),
        variants=builds,
        pages=tuple(read),
        parts_read=len(found),
        suspect=suspect,
        supplements=bool(known),
    )
