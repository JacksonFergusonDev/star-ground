"""Finds what the self-checks look for on a page.

Footnote marks on values, designators printed without a value, rows OCR
couldn't read, and parts lists drawn as pictures.
"""

import math
import re
from itertools import pairwise

from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.pdf.cells import REF_RANGE, is_ref_cell
from star_ground.bom_lib.pdf.geometry import Cell, group_lines, page_lines
from star_ground.bom_lib.pdf.scan import find_listings
from star_ground.bom_lib.types import PDFWord, Picture
from star_ground.bom_lib.utils import expand_refs

# A footnote marker is a short number set at most this fraction of the size of
# the word it follows (Aion FX legacy: '68k' at 11pt, marker '1' at 6.4pt).
_SUPERSCRIPT_RATIO = 0.75
_MARKS = re.compile(r"\d{1,2}(?:,\d{1,2})*,?")
# ... within this many ems after it: after the designator when it stands alone.
_ALONE_EM = 6.0


def footnote_marks(words: list[PDFWord]) -> list[PDFWord]:
    """Superscript footnote markers printed right after a word.

    'R2  68k¹' and 'IC1  LT1054CP⁵' would otherwise read as values '68k 1'
    and 'LT1054CP 5' (Aion FX legacy documents). A marker can also stand alone
    in a value cell ('D6-D9  ³', 'your choice'), where it would read as the
    value '3'.

    Args:
        words: Words extracted from one page.

    Returns:
        The marker words, so the caller can drop their characters.
    """
    return [
        w
        for w in words
        # 'Volume  100kA ³˒⁶' marks two footnotes.
        if _MARKS.fullmatch(w["text"])
        and any(
            v is not w
            and w["size"] <= _SUPERSCRIPT_RATIO * v["size"]
            and 0 <= w["x0"] - v["x1"] <= _ALONE_EM * v["size"]
            and abs((v["top"] + v["size"]) - (w["top"] + w["size"])) < v["size"]
            for v in words
        )
    ]


def marked_labels(words: list[PDFWord]) -> list[tuple[str, str]]:
    """The label of each table row whose value carries a footnote marker.

    Aion FX legacy Lumin lists 'R15  15k¹' and 'Midrange  50kB¹', and footnote 1
    says how to build without the Midrange knob. The markers are dropped before
    reading (`footnote_marks`), so this records which row each one marked.

    Args:
        words: Words extracted from one page, markers included.

    Returns:
        (row label, marker) pairs: ('R15', '1'), ('High Freq', '2').
    """
    marks = footnote_marks(words)
    if not marks:
        return []
    found: list[tuple[str, str]] = []
    for cells in page_lines(words):
        for k, cell in enumerate(cells):
            for word in cell.words:
                if any(word is m for m in marks) and k > 0:
                    label = cells[k - 1].text
                    if cell.words[0] is word and k > 1:
                        label = cells[k - 2].text  # the marker is a cell of its own
                    found += [(label, n) for n in word["text"].split(",") if n]
    return found


# A designator column holds at least this many listings.
_VALUELESS_COLUMN = 3
# A footnote number is set at most this fraction of its row's type size.
_MARKER_SIZE = 0.8
_VALUELESS_ALIGN_PT = 2.0


def valueless_refs(words: list[PDFWord]) -> list[str]:
    """Designators in a parts table's designator column printed with no value.

    'D6-D9  ³' leaves the value to the builder (Aion FX legacy Cirrus); with
    its footnote marker dropped, it is no listing, and would vanish silently.

    Args:
        words: Words extracted from one page.

    Returns:
        The designators, ranges expanded.
    """
    lines = page_lines(words)
    listings = find_listings(words)
    starts = {(lst.line, round(lst.x0)) for lst in listings}
    edges: list[tuple[float, float]] = [
        (cell.x0, cell.words[-1]["x1"])
        for lst in listings
        for cell in lines[lst.line]
        if abs(cell.x0 - lst.x0) <= _VALUELESS_ALIGN_PT
    ]

    def in_column(cell: Cell) -> bool:
        x1 = cell.words[-1]["x1"]
        lefts = sum(abs(cell.x0 - a) <= _VALUELESS_ALIGN_PT for a, _ in edges)
        rights = sum(abs(x1 - b) <= _VALUELESS_ALIGN_PT for _, b in edges)
        return max(lefts, rights) >= _VALUELESS_COLUMN

    refs: list[str] = []
    for number, cells in enumerate(lines):
        for k, cell in enumerate(cells):
            text = cell.text
            if (number, round(cell.x0)) in starts or not in_column(cell):
                continue
            ranged = REF_RANGE.fullmatch(text) is not None
            if not (ranged or is_designator(text)):
                continue
            after = cells[k + 1] if k + 1 < len(cells) else None
            # A cell beside it is its value, unless it is only a footnote
            # number: 'DX1-2  ²' (Aion FX legacy Cerulean).
            if (
                after is not None
                and not in_column(after)
                and not _is_marker(after, cell)
            ):
                continue
            # A value in smaller type sits a little off the line ('RX2  jumper¹').
            end, middle = cell.words[-1]["x1"], _middle(cell.words[0])
            stop = after.x0 if after else math.inf
            if any(
                end < w["x0"] < stop
                and abs(_middle(w) - middle) < cell.words[0]["size"]
                for w in words
            ):
                continue
            refs += expand_refs(text) if ranged else [text]
    return refs


# OCR's boxes start this far apart within one column.
_OCR_ALIGN_PT = 6.0
# A misread designator is short: 'Q9' read as '60'.
_UNREADABLE_CHARS = 4
# A part number ('MPF102', '2N5457', 'TL072') or a value ('4k7', '100n').
_PART_LIKE = re.compile(
    r"[A-Z]{1,4}-?\d{2,}[A-Z0-9-]*|\d+[A-Z]\d+[A-Z]*|\d+(?:\.\d+)?[pnuµkKMR]\w*"
)


def unreadable_rows(words: list[PDFWord]) -> list[str]:
    """Rows of a parts table whose designator cell is no designator.

    OCR reads GuitarPCB Guitar/Bass Driver's 'Q9  MPF102' as '60  MPF102': the
    value sits in the value column, but nothing names its part, so the row
    is dropped. Only for OCR'd pages, whose text is uncertain.

    Args:
        words: Words read from one page.

    Returns:
        Each such row's text.
    """
    lines = page_lines(words)
    values: list[float] = []
    for lst in find_listings(words):
        cells = lines[lst.line]
        k = next(
            (
                i
                for i, c in enumerate(cells)
                if abs(c.x0 - lst.x0) <= _VALUELESS_ALIGN_PT
            ),
            None,
        )
        if k is not None and k + 1 < len(cells):
            values.append(cells[k + 1].x0)
    rows: list[str] = []
    for cells in lines:
        for label, value in pairwise(cells):
            aligned = sum(abs(value.x0 - x) <= _OCR_ALIGN_PT for x in values)
            text = label.text
            if (
                aligned >= _VALUELESS_COLUMN
                and len(text) <= _UNREADABLE_CHARS
                and any(ch.isdigit() for ch in text)
                and not is_ref_cell(text, value.text)
                and _PART_LIKE.fullmatch(value.text) is not None
            ):
                rows.append(f"{text}  {value.text}")
    return rows


def _is_marker(cell: Cell, beside: Cell) -> bool:
    """Whether a cell is only a footnote number, in smaller type than its row."""
    size = beside.words[0]["size"] * _MARKER_SIZE
    return cell.text.isdigit() and all(w["size"] < size for w in cell.words)


def _middle(word: PDFWord) -> float:
    return word["top"] + word["size"] / 2


# 'Bill of Materials Sonic Bloom:', 'PARTS LIST', 'BOM'.
_LIST_HEADING = re.compile(r"(?:bill of materials|parts list|bom)\b", re.IGNORECASE)
# The picture starts right under its heading, within this many line heights.
_PICTURE_GAP_EM = 3.0


def pictured_lists(words: list[PDFWord], pictures: list[Picture]) -> list[str]:
    """Parts-list headings whose list is a picture, not text.

    GuitarPCB Sonic Architect heads two boards' lists 'Bill of Materials Sonic
    Bloom:' and 'Bill of Materials AmpEQ:'; the first is an image right under
    its heading, with no text inside it, so the text layer never reads it.

    Args:
        words: Words extracted from one page.
        pictures: The page's large images.

    Returns:
        The heading of every such list, top to bottom.
    """
    found = []
    for line in group_lines(words):
        text = " ".join(w["text"] for w in line)
        if not _LIST_HEADING.match(text):
            continue
        bottom = max(w["top"] + w["size"] for w in line)
        left = line[0]["x0"]
        for pic in pictures:
            if not (
                0 <= pic.top - bottom <= _PICTURE_GAP_EM * line[0]["size"]
                and pic.x0 - line[0]["size"] <= left <= pic.x1
            ):
                continue
            inside = any(
                pic.x0 <= w["x0"] <= pic.x1 and pic.top <= w["top"] <= pic.bottom
                for w in words
            )
            if not inside:
                found.append(text.rstrip(":"))
                break
    return found
