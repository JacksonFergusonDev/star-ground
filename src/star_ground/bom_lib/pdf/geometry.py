"""Groups a page's positioned words into lines and cells."""

from dataclasses import dataclass, field

from star_ground.bom_lib.types import PDFWord

# Gaps wider than this fraction of the font size separate cells. Measured on
# PedalPCB documents: spaces inside a value are ~0.3em, column gaps >= 0.6em.
_CELL_GAP_EM = 0.45
# Words whose tops differ by less than this fraction of the font size share a
# line. Words on a real line differ by <= 0.05em; an adjacent footnote line in
# The Abyss sits only 0.19em away.
LINE_TOLERANCE_EM = 0.15


@dataclass(frozen=True, slots=True)
class Cell:
    """A run of words separated from its neighbours by a column-sized gap.

    Attributes:
        words: The words in the cell, left to right.
    """

    words: tuple[PDFWord, ...]

    @property
    def text(self) -> str:
        """The cell's text, words joined by single spaces."""
        return " ".join(w["text"] for w in self.words)

    @property
    def x0(self) -> float:
        """Left edge of the cell, in points."""
        return self.words[0]["x0"]

    @property
    def size(self) -> float:
        """Font size of the cell's first word, in points."""
        return self.words[0]["size"]


@dataclass(frozen=True, slots=True)
class Listing:
    """A designator and the value printed beside it.

    Position fields locate the listing for build analysis (variant grids,
    option groups) and do not take part in equality.

    Attributes:
        ref: The designator (e.g. 'R1', 'VOLUME').
        value: The full value, including qualifiers (e.g. '1u MLCC').
        line: Index of the visual line on the page (see `page_lines`).
        x0: Left edge of the designator, in points.
        dashed: Whether a dash separates designator and value ('R1 - 10k').
    """

    ref: str
    value: str
    line: int = field(default=0, compare=False)
    x0: float = field(default=0.0, compare=False)
    dashed: bool = field(default=False, compare=False)


def group_lines(words: list[PDFWord]) -> list[list[PDFWord]]:
    """Groups words into visual lines, each sorted left to right.

    Args:
        words: Words extracted from one page.

    Returns:
        Lines of words, top to bottom.
    """
    lines: list[list[PDFWord]] = []
    for word in sorted(words, key=lambda w: w["top"]):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= (
            LINE_TOLERANCE_EM * word["size"]
        ):
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def split_cells(line: list[PDFWord]) -> list[Cell]:
    """Splits one line into cells at column-sized gaps.

    Args:
        line: Words on one line, sorted left to right.

    Returns:
        The cells, left to right.
    """
    cells: list[list[PDFWord]] = []
    prev: PDFWord | None = None
    for word in line:
        gap = word["x0"] - prev["x1"] if prev else 0.0
        if prev is None or gap > _CELL_GAP_EM * max(word["size"], prev["size"]):
            cells.append([word])
        else:
            cells[-1].append(word)
        prev = word
    return [Cell(tuple(c)) for c in cells]


def page_lines(words: list[PDFWord]) -> list[list[Cell]]:
    """The cells of every visual line on a page, numbered as `find_listings` numbers them."""
    return [split_cells(line) for line in group_lines(words)]
