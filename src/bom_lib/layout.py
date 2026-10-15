"""Layout-aware extraction of designator/value pairs from PDF text.

Parts lists are laid out in visual columns ("R1  1M   C1  100n   Q1  BS170").
Plain text extraction flattens those columns into one line, which makes it
impossible to tell where a multi-word value ("3mm Red LED") ends and the next
column begins. This module works from word positions instead:

1. Words are grouped into lines by vertical position.
1. Each line is split into *cells* wherever the horizontal gap between words is
   wider than an ordinary space.
1. A cell holding exactly one designator, followed by a value cell, is a
   listing. Annotation cells that follow ("(25V or higher)") belong to it.

Prose ("D8 and D9 are only needed...") never yields a listing, because its
words are separated by ordinary spaces and so form a single cell.
"""

import re
from dataclasses import dataclass, field

from src.bom_lib import constants
from src.bom_lib.designators import BOARD_REF, is_designator
from src.bom_lib.types import PDFWord

# Gaps wider than this fraction of the font size separate cells. Measured on
# PedalPCB documents: spaces inside a value are ~0.3em, column gaps >= 0.6em.
_CELL_GAP_EM = 0.45
# Words whose tops differ by less than this fraction of the font size share a
# line. Words on a real line differ by <= 0.05em; an adjacent footnote line in
# The Abyss sits only 0.19em away.
_LINE_TOLERANCE_EM = 0.15

# Unfamiliar numbered labels (CTRL1, FUZZ2) that are not standard designators.
_NUMBERED_LABEL = re.compile(r"[A-Z]{1,5}\d{1,3}")
_CONTROL_NAME = re.compile(r"[A-Z][A-Z0-9_-]{1,11}")
_MULTIWORD_NAME = re.compile(r"[A-Z]{2,}(?: [A-Z]{2,}){1,2}")
# A whole-cell parenthetical on the next line, aligned with a value above it,
# continues that value ('SPDT Toggle' / '(On/On)').
_CONTINUATION = re.compile(r"\(.*\)")
_CONTROL_VALUE = re.compile(
    r"(?:[ABCWG]\d+(?:\.\d+)?[KM]\b|\d+(?:\.\d+)?[KM][ABC]?\b|[SD34]P[DS]T\b|\dP\dT\b)"
)

# Column headers and connective words in multi-column parts lists.
_LAYOUT_WORDS = frozenset(
    {
        "RESISTORS",
        "CAPACITORS",
        "SEMICONDUCTORS",
        "DIODES",
        "TRANSISTORS",
        "INTEGRATED",
        "CIRCUITS",
        "POTENTIOMETERS",
        "CRYSTALS",
        "SWITCHES",
        "SWITCH",
        "TOGGLE",
        "ROTARY",
        "LDRS",
        "OR",
    }
)
# Section headers that also appear beside values. Used only to stop a value
# from absorbing them; 'TRIM' alone is still a designator.
_HEADER_WORDS = _LAYOUT_WORDS | {
    "TRIM",
    "POTS",
    "(3362P)",
    "(3362P",
    "TYPE)",
    "TRANSFORMERS",
    "OPTICAL",
    "MODIFICATIONS",
    "SMD",
    "THROUGH-HOLE",
    "DIP",
}
# A merged 'NAME value' cell is split only when the value is short
# ('DPDT (On / Off / On)' is six words).
_MAX_MERGED_VALUE_WORDS = 6
# A function label printed between a designator and its value: 'SW1 (Power) SPDT'.
_LABEL = re.compile(r"\([^()\d]*\)")
_LED_WORD = re.compile(r"\bLED\b", re.IGNORECASE)
# Two or more lowercase words mark a sentence ('Choose between ... or SMD').
_LOWERCASE_WORD = re.compile(r"[a-z][a-z'-]*[.,;:]?")
_ASIDE = re.compile(r"\(see notes?\)", re.IGNORECASE)
# Trailing words that only restate the component type.
_REDUNDANT_SUFFIX = re.compile(r"\s+(?:Crystal|Switch)$", re.IGNORECASE)


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
    """

    ref: str
    value: str
    line: int = field(default=0, compare=False)
    x0: float = field(default=0.0, compare=False)


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
            _LINE_TOLERANCE_EM * word["size"]
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


def _has_digit(cell: str | None) -> bool:
    return cell is not None and any(ch.isdigit() for ch in cell)


def _is_ref_cell(cell: str, next_cell: str | None) -> bool:
    if cell in _LAYOUT_WORDS:
        return False
    if cell in constants.BARE_DESIGNATORS:
        return _is_bare_value(next_cell)
    if is_designator(cell) or _NUMBERED_LABEL.fullmatch(cell):
        return True
    if cell in constants.KEYWORDS:
        return True
    # Unfamiliar control names (SHIFT, SUBS, TONE BYPASS SWITCH) are recognized
    # by their value.
    named = _CONTROL_NAME.fullmatch(cell) is not None or (
        _MULTIWORD_NAME.fullmatch(cell) is not None
        and not set(cell.split()) <= _HEADER_WORDS  # 'TOGGLE SWITCHES' is a header
    )
    return (
        next_cell is not None and named and _CONTROL_VALUE.match(next_cell) is not None
    )


def _starts_listing(cell: str, next_cell: str | None) -> bool:
    """Whether a cell in value position is really the next designator."""
    if BOARD_REF.fullmatch(cell):
        return True
    # Directly after a designator a bare 'LED' is that designator's value
    # ('LED_A  LED  Indicator LED'), unless a numeric value follows it.
    return cell in constants.BARE_DESIGNATORS and _has_digit(next_cell)


def _is_bare_value(cell: str | None) -> bool:
    """Whether a cell can be the value of a bare designator ('LED', 'CLR').

    'LED' is also an ordinary value ('D3  LED'); as a designator it is followed
    by a numeric value ('LED  3mm Red LED') or an LED description that does
    not repeat it on its own ('LED  Indicator LED').
    """
    if cell is None:
        return False
    return _has_digit(cell) or (
        cell.upper() != "LED" and _LED_WORD.search(cell) is not None
    )


def _is_prose(cell: str) -> bool:
    words = cell.split()
    lowercase = sum(1 for w in words if _LOWERCASE_WORD.fullmatch(w))
    return not cell.startswith("(") and lowercase >= 2


def _is_annotation(cell: str) -> bool:
    words = cell.split()
    return not (
        cell.startswith("*")
        or _is_prose(cell)
        or all(w.upper() in _HEADER_WORDS for w in words)
        or BOARD_REF.fullmatch(words[0]) is not None
    )


def clean_value(value: str) -> str:
    """Strips markers that are not part of a purchasable value.

    Removes optional-part asterisks, '(see notes)' asides, and trailing words
    that only restate the component type ('AB38T Crystal' -> 'AB38T').

    Args:
        value: The raw value text.

    Returns:
        The cleaned value.
    """
    value = _ASIDE.sub("", value).replace("*", " ")
    value = " ".join(value.split())
    # A leading function label is not part of the value: '(Voltage) SPDT Toggle'.
    label = _LABEL.match(value)
    if label and label.end() < len(value):
        value = value[label.end() :].strip()
    return _REDUNDANT_SUFFIX.sub("", value)


def _split_merged(cell: Cell) -> list[Cell]:
    """Splits a 'NAME value' cell that a narrow gap failed to separate.

    In tab-aligned columns a long control name can end within an ordinary
    word space of its value ('BLEND B10K'), so both land in one cell. Such a
    cell is split when its first word is a designator or control name and the
    rest is a short value; prose ('D8 and D9 are only needed...') is left whole.
    """
    words = cell.text.split()
    if len(words) < 2 or len(words) > _MAX_MERGED_VALUE_WORDS + 1:
        return [cell]
    name = words[0]
    # Skip a function label: 'SW1 (Power) SPDT Toggle' -> 'SPDT Toggle'.
    start = 1
    label = next(
        (
            k
            for k in range(1, len(words))
            if _LABEL.fullmatch(" ".join(words[1 : k + 1]))
        ),
        None,
    )
    if label is not None and label + 1 == len(words) and is_designator(name):
        # 'SW1 (Power)' | 'SPDT Toggle': the label belongs to the designator's
        # cell and the value is the next cell.
        return [Cell(cell.words[:1])]
    if label is not None and label + 1 < len(words):
        start = label + 1
    rest = " ".join(words[start:])
    value_like = (
        _has_digit(words[start])
        or _CONTROL_VALUE.match(rest) is not None
        # 'C16 Not Populated' in a modification list.
        or rest.upper() in constants.NOT_POPULATED_VALUES
    )
    if not value_like:
        return [cell]
    split = [Cell(cell.words[:1]), Cell(cell.words[start:])]
    if is_designator(name) and name not in constants.BARE_DESIGNATORS:
        return split
    if name in constants.KEYWORDS or (
        _CONTROL_NAME.fullmatch(name) and _CONTROL_VALUE.match(rest)
    ):
        return split
    return [cell]


def page_lines(words: list[PDFWord]) -> list[list[Cell]]:
    """The cells of every visual line on a page, numbered as `find_listings` numbers them."""
    return [split_cells(line) for line in group_lines(words)]


def find_listings(words: list[PDFWord]) -> list[Listing]:
    """Finds every designator/value pair on a page.

    Args:
        words: Words extracted from one page.

    Returns:
        Listings in reading order (top to bottom, left to right).
    """
    listings: list[Listing] = []
    # (value x-position, font size, index into listings) for the previous line.
    above: list[tuple[float, float, int]] = []
    for line_no, line in enumerate(group_lines(words)):
        cells = [part for cell in split_cells(line) for part in _split_merged(cell)]
        cells = _absorb_continuations(cells, above, listings)
        texts = [c.text for c in cells]
        current: list[tuple[float, float, int]] = []
        i = 0
        while i < len(texts):
            nxt = texts[i + 1] if i + 1 < len(texts) else None
            after = texts[i + 2] if i + 2 < len(texts) else None
            if (
                nxt is None
                or not _is_ref_cell(texts[i], nxt)
                or _starts_listing(nxt, after)
            ):
                i += 1
                continue
            parts = [nxt]
            j = i + 2
            while j < len(texts):
                following = texts[j + 1] if j + 1 < len(texts) else None
                if _is_ref_cell(texts[j], following) or not _is_annotation(texts[j]):
                    break
                parts.append(texts[j])
                j += 1
            value = clean_value(" ".join(parts))
            if value:
                current.append((cells[i + 1].x0, cells[i + 1].size, len(listings)))
                listings.append(
                    Listing(ref=texts[i], value=value, line=line_no, x0=cells[i].x0)
                )
            i = j
        above = current
    return listings


def _absorb_continuations(
    cells: list[Cell],
    above: list[tuple[float, float, int]],
    listings: list[Listing],
) -> list[Cell]:
    """Appends wrapped parentheticals to the value they continue.

    A cell that is entirely parenthetical and starts at the same x-position as
    a value on the line above ('SPDT Toggle' / '(On/On)') is the rest of that
    value, not a new entry.

    Returns:
        The cells that remain after continuations are absorbed.
    """
    remaining: list[Cell] = []
    for cell in cells:
        target = next(
            (
                idx
                for x0, size, idx in above
                if abs(cell.x0 - x0) <= _LINE_TOLERANCE_EM * size
            ),
            None,
        )
        if target is not None and _CONTINUATION.fullmatch(cell.text):
            prev = listings[target]
            listings[target] = Listing(
                prev.ref, clean_value(f"{prev.value} {cell.text}"), prev.line, prev.x0
            )
        else:
            remaining.append(cell)
    return remaining
