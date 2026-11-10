"""Finds what the self-checks look for on a page.

Footnote marks on values, designators printed without a value, rows OCR
couldn't read, and parts lists drawn as pictures.
"""

import math
import re
from bisect import bisect_left, bisect_right
from itertools import pairwise

from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.pdf.cells import REF_RANGE, is_ref_cell
from star_ground.bom_lib.pdf.geometry import Cell, group_lines, page_lines
from star_ground.bom_lib.pdf.page import PDFPageExtraction
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
    # A marker is compared only with the words in the few grid cells it could
    # follow, not with every word on the page: a page of thousands of small
    # numbers would otherwise take minutes. Words are filed by size, within a
    # factor of two, on a grid scaled to that size, so a word's reach (one
    # size up or down, `_ALONE_EM` sizes right) spans a fixed few cells.
    grid: dict[float, dict[tuple[int, int], list[PDFWord]]] = {}
    largest: dict[float, float] = {}
    for v in words:
        # A word of no size marks nothing (the baseline test needs a size).
        if not (0 < v["size"] < math.inf and _finite(v["x1"], _baseline(v))):
            continue
        cell = size_cell(v["size"])
        key = (
            math.floor(v["x1"] / (_ALONE_EM * cell)),
            math.floor(_baseline(v) / cell),
        )
        grid.setdefault(cell, {}).setdefault(key, []).append(v)
        largest[cell] = max(largest.get(cell, 0.0), v["size"])

    def marked(w: PDFWord) -> bool:
        if not _finite(w["x0"], _baseline(w)):
            return False
        for cell, cells in grid.items():
            if w["size"] > _SUPERSCRIPT_RATIO * largest[cell]:
                continue
            gx = math.floor(w["x0"] / (_ALONE_EM * cell))
            gy = math.floor(_baseline(w) / cell)
            # A size is under two cells, so its reach is under two cells
            # left and two up or down; one more each way absorbs rounding.
            for x in range(gx - 3, gx + 1):
                for y in range(gy - 3, gy + 4):
                    if any(
                        v is not w
                        and w["size"] <= _SUPERSCRIPT_RATIO * v["size"]
                        and 0 <= w["x0"] - v["x1"] <= _ALONE_EM * v["size"]
                        and abs(_baseline(v) - _baseline(w)) < v["size"]
                        for v in cells.get((x, y), ())
                    ):
                        return True
        return False

    # 'Volume  100kA ³˒⁶' marks two footnotes.
    return [w for w in words if _MARKS.fullmatch(w["text"]) and marked(w)]


def _baseline(word: PDFWord) -> float:
    return word["top"] + word["size"]


def size_cell(size: float) -> float:
    """The side of a grid cell for filing words of a size, in points.

    It is the power of two at or below the size, so sizes within a factor of
    two share a grid and a size is less than two cells. It is at least a
    point, so a vanishingly small word doesn't make a grid of countless cells
    (all sizes under two points share one grid).

    Args:
        size: A word's size, finite.

    Returns:
        The cell's side.
    """
    return max(1.0, math.ldexp(1.0, math.frexp(size)[1] - 1))


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def marked_labels(words: list[PDFWord], marks: list[PDFWord]) -> list[tuple[str, str]]:
    """The label of each table row whose value carries a footnote marker.

    Aion FX legacy Lumin lists 'R15  15k¹' and 'Midrange  50kB¹', and footnote 1
    says how to build without the Midrange knob. The markers are dropped before
    reading (`footnote_marks`), so this records which row each one marked.

    Args:
        words: Words extracted from one page, markers included.
        marks: The page's markers, from `footnote_marks(words)`.

    Returns:
        (row label, marker) pairs: ('R15', '1'), ('High Freq', '2').
    """
    if not marks:
        return []
    marked = {id(m) for m in marks}
    found: list[tuple[str, str]] = []
    for cells in page_lines(words):
        for k, cell in enumerate(cells):
            for word in cell.words:
                if id(word) in marked and k > 0:
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


def valueless_refs(page: PDFPageExtraction) -> list[str]:
    """Designators in a parts table's designator column printed with no value.

    'D6-D9  ³' leaves the value to the builder (Aion FX legacy Cirrus); with
    its footnote marker dropped, it is no listing, and would vanish silently.

    Args:
        page: One page.

    Returns:
        The designators, ranges expanded.
    """
    lines, listings = page.lines, page.listings
    starts = {(lst.line, round(lst.x0)) for lst in listings}
    edges = [
        (cell.x0, cell.words[-1]["x1"])
        for lst in listings
        for cell in lines[lst.line]
        if abs(cell.x0 - lst.x0) <= _VALUELESS_ALIGN_PT
    ]
    lefts = sorted(a for a, _ in edges if math.isfinite(a))
    rights = sorted(b for _, b in edges if math.isfinite(b))

    def in_column(cell: Cell) -> bool:
        return (
            max(_aligned(lefts, cell.x0), _aligned(rights, cell.words[-1]["x1"]))
            >= _VALUELESS_COLUMN
        )

    between = _WordsBetween(page.words)

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
            if between.any(end, stop, middle, cell.words[0]["size"]):
                continue
            refs += expand_refs(text) if ranged else [text]
    return refs


def _aligned(edges: list[float], x: float) -> int:
    """How many of the sorted edges are within `_VALUELESS_ALIGN_PT` of x.

    `edge - x` only grows with the edge, so the edges within reach are one
    run of the list, found by bisection.
    """
    reach = _VALUELESS_ALIGN_PT
    if math.isnan(x):
        return 0
    return bisect_right(edges, reach, key=lambda a: a - x) - bisect_left(
        edges, -reach, key=lambda a: a - x
    )


class _WordsBetween:
    """Whether any word of a page starts between two x positions near a line.

    The words are sorted by their middle, and a segment tree holds the left
    edges of each run of them, sorted, so a question takes two bisections per
    tree node it visits (O(log² n)) instead of a pass over the page.
    """

    def __init__(self, words: list[PDFWord]) -> None:
        """Indexes a page's words.

        Args:
            words: Words extracted from one page.
        """
        placed = sorted(
            (
                (_middle(w), w["x0"])
                for w in words
                if math.isfinite(_middle(w)) and math.isfinite(w["x0"])
            ),
        )
        self._middles = [m for m, _ in placed]
        self._leaves = 1 << max(0, len(placed) - 1).bit_length()
        self._tree: list[list[float]] = [[] for _ in range(2 * self._leaves)]
        for k, (_, x0) in enumerate(placed):
            self._tree[self._leaves + k] = [x0]
        for k in range(self._leaves - 1, 0, -1):
            self._tree[k] = sorted(self._tree[2 * k] + self._tree[2 * k + 1])

    def any(self, left: float, right: float, middle: float, size: float) -> bool:
        """Whether a word starts between left and right, near a line.

        Args:
            left: The word starts right of this...
            right: ...and left of this.
            middle: The middle of the line, in points from the top.
            size: The word's middle is less than this from the line's.

        Returns:
            Whether there is such a word.
        """
        # `m - middle` only grows with m, so the near words are one run.
        lo = bisect_right(self._middles, -size, key=lambda m: m - middle)
        hi = bisect_left(self._middles, size, key=lambda m: m - middle)
        lo, hi = lo + self._leaves, hi + self._leaves
        while lo < hi:
            if lo & 1:
                if self._starts(self._tree[lo], left, right):
                    return True
                lo += 1
            if hi & 1:
                hi -= 1
                if self._starts(self._tree[hi], left, right):
                    return True
            lo, hi = lo >> 1, hi >> 1
        return False

    @staticmethod
    def _starts(x0s: list[float], left: float, right: float) -> bool:
        k = bisect_right(x0s, left)
        return k < len(x0s) and x0s[k] < right


# OCR's boxes start this far apart within one column.
_OCR_ALIGN_PT = 6.0
# A misread designator is short: 'Q9' read as '60'.
_UNREADABLE_CHARS = 4
# A part number ('MPF102', '2N5457', 'TL072') or a value ('4k7', '100n').
_PART_LIKE = re.compile(
    r"[A-Z]{1,4}-?\d{2,}[A-Z0-9-]*|\d+[A-Z]\d+[A-Z]*|\d+(?:\.\d+)?[pnuµkKMR]\w*"
)


def unreadable_rows(page: PDFPageExtraction) -> list[str]:
    """Rows of a parts table whose designator cell is no designator.

    OCR reads GuitarPCB Guitar/Bass Driver's 'Q9  MPF102' as '60  MPF102': the
    value sits in the value column, but nothing names its part, so the row
    is dropped. Only for OCR'd pages, whose text is uncertain.

    Args:
        page: One page, read by OCR.

    Returns:
        Each such row's text.
    """
    lines = page.lines
    values: list[float] = []
    for lst in page.listings:
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
