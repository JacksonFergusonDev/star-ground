"""Reads rows that count parts by value, with no designator ('3 - 1N914', Tonepad)."""

import re
from dataclasses import dataclass

from star_ground.bom_lib.pdf.geometry import Cell, page_lines
from star_ground.bom_lib.types import PDFWord

# A row that counts parts instead of naming them: '1 - 25k lin.' (Tonepad).
COUNTED_ROW = re.compile(r"\d{1,2}\s*[-\u2013]\s+\S")


def counted_rows(lines: list[list[Cell]]) -> int:
    """How many cells count parts by value ('2 - 1k', Tonepad)."""
    return sum(COUNTED_ROW.match(c.text) is not None for line in lines for c in line)


@dataclass(frozen=True, slots=True)
class CountedRow:
    """A parts list row that counts parts by value instead of naming them.

    Tonepad lists '4 - 10k' under a Resistors heading: four 10k resistors,
    with no designators.

    Attributes:
        count: How many of the part to buy.
        value: The value as printed ('1k5 (1.5k)', '100k Lin').
        heading: The column's heading above the row ('Resistors',
            'Transistors &ICs'), if any.
        line: Index of the visual line on the page (see `page_lines`).
        x0: Left edge of the row, in points.
    """

    count: int
    value: str
    heading: str | None
    line: int
    x0: float

    @property
    def text(self) -> str:
        """The row as printed: '4 - 10k'."""
        return f"{self.count} - {self.value}"


# A whole cell that counts parts: '4 - 10k', '1 - 0.022 µF' (Tonepad).
_COUNTED_CELL = re.compile(r"(\d{1,2})\s*[-\u2013]\s+(\S.*)")
# A dashed listing above a counted row in its column: 'IC1 - LM308'.
_DASHED_CELL = re.compile(r"\S+\s*[-\u2013]\s")
# A word qualifying the value before it: a taper or a capacitor type.
_QUALIFIER_CELL = re.compile(r"(?i)(?:lin|log|rev\.? ?log|tant|trim|np)\.?")
# How far a heading's left edge may sit from its column's, in points.
_HEADING_ALIGN_PT = 12.0


def counted_parts(words: list[PDFWord]) -> list[CountedRow]:
    """Finds every row that counts parts by value, with its column heading.

    A zero count is a switch position, not a part ('0 - Chorus-reverb',
    Tonepad Digital Reverb).

    Args:
        words: Words extracted from one page.

    Returns:
        Counted rows in reading order.
    """
    lines = page_lines(words)
    rows = []
    for k, cells in enumerate(lines):
        for i, cell in enumerate(cells):
            m = _COUNTED_CELL.fullmatch(cell.text)
            if m is None or int(m.group(1)) == 0:
                continue
            value = m.group(2).strip()
            # A taper set a gap apart: '1 - 10k | Log' (Tonepad Tube Sound Fuzz).
            after = cells[i + 1].text if i + 1 < len(cells) else ""
            if _QUALIFIER_CELL.fullmatch(after):
                value = f"{value} {after}"
            rows.append(
                CountedRow(
                    int(m.group(1)),
                    value,
                    _column_heading(lines, k, cell.x0),
                    k,
                    cell.x0,
                )
            )
    return rows


def _column_heading(lines: list[list[Cell]], line: int, x0: float) -> str | None:
    """The text heading a column of rows: the first cell above that is no row."""
    for k in range(line - 1, -1, -1):
        above = [c for c in lines[k] if abs(c.x0 - x0) <= _HEADING_ALIGN_PT]
        if not above:
            continue
        text = above[0].text
        if _COUNTED_CELL.fullmatch(text) or _DASHED_CELL.match(text):
            continue
        return text
    return None
