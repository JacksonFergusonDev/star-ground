"""One page as the readers see it: its tables, text and positioned words.

A PDF's text layer and an OCR'd page image both arrive as a
`PDFPageExtraction`. The scans several readers and self-checks need (the
page's lines, its listings, its typed tables) are made from its words the
first time one asks, and kept on the page for the rest of the reading.
"""

from dataclasses import dataclass, field
from functools import cached_property

from star_ground.bom_lib.pdf.bom_table import BomReading, read_bom
from star_ground.bom_lib.pdf.geometry import Cell, Listing, page_lines
from star_ground.bom_lib.pdf.scan import find_listings
from star_ground.bom_lib.types import PDFWord, Picture


@dataclass(frozen=True)
class PDFPageExtraction:
    """Extracted text and tabular layout from a single page.

    The page's scans are made from `words` once, when first asked for, and
    shared by every reader of the page; nothing may change `words` or the
    scans afterwards. A page with other words is another page
    (`dataclasses.replace`), with scans of its own.

    Attributes:
        tables: List of extracted tables (each table is rows of string cells or None).
        text: The page's text, empty when it has none.
        words: Positioned words, used for column-aware layout analysis.
        labels: Words printed in the margin beside a table, which name parts
            of it ('Standard RAT' beside one build's rows); OCR keeps them
            apart from the table's words.
        pictures: The page's large images, where a parts table may be drawn
            as a picture rather than text.
        marked: Rows whose value carries a numbered footnote marker, as
            (row label, marker) pairs (see `pdf.page_checks.marked_labels`).
    """

    tables: list[list[list[str | None]]]
    text: str
    words: list[PDFWord]
    labels: list[PDFWord] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    marked: list[tuple[str, str]] = field(default_factory=list)

    @cached_property
    def lines(self) -> list[list[Cell]]:
        """The cells of every visual line on the page (`page_lines`)."""
        return page_lines(self.words)

    @cached_property
    def listings(self) -> list[Listing]:
        """Every designator/value pair on the page (`find_listings`)."""
        return find_listings(self.lines)

    @cached_property
    def bom(self) -> BomReading:
        """The page's typed text tables (`read_bom`)."""
        return read_bom(self.lines)
