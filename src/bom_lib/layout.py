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
from dataclasses import dataclass

from src.bom_lib import constants
from src.bom_lib.types import PDFWord

# Gaps wider than this fraction of the font size separate cells. Measured on
# PedalPCB documents: spaces inside a value are ~0.3em, column gaps >= 0.6em.
_CELL_GAP_EM = 0.45
# Words whose tops differ by less than this fraction of the font size share a
# line. Words on a real line differ by <= 0.05em; an adjacent footnote line in
# The Abyss sits only 0.19em away.
_LINE_TOLERANCE_EM = 0.15

_NUMBERED_REF = re.compile(r"[A-Z]{1,5}\d{1,3}")
# Designators that can never be a part number. Part numbers share the
# letters-then-digits shape (TL072, BS170, J201), so only these short board
# prefixes are trusted to start a new listing in the value position.
_BOARD_REF = re.compile(r"(?:R|C|D|Q|IC|U|SW|LED|LDR|X|Y|VR|TRIM)\d{1,3}")
_CONTROL_NAME = re.compile(r"[A-Z][A-Z0-9]{1,11}")
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
_ASIDE = re.compile(r"\(see notes?\)", re.IGNORECASE)
# Trailing words that only restate the component type.
_REDUNDANT_SUFFIX = re.compile(r"\s+(?:Crystal|Switch)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Listing:
    """A designator and the value printed beside it.

    Attributes:
        ref: The designator (e.g. 'R1', 'VOLUME').
        value: The full value, including qualifiers (e.g. '1u MLCC').
    """

    ref: str
    value: str


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


def split_cells(line: list[PDFWord]) -> list[str]:
    """Splits one line into cells at column-sized gaps.

    Args:
        line: Words on one line, sorted left to right.

    Returns:
        The text of each cell.
    """
    cells: list[list[str]] = []
    prev: PDFWord | None = None
    for word in line:
        gap = word["x0"] - prev["x1"] if prev else 0.0
        if prev is None or gap > _CELL_GAP_EM * max(word["size"], prev["size"]):
            cells.append([word["text"]])
        else:
            cells[-1].append(word["text"])
        prev = word
    return [" ".join(c) for c in cells]


def _has_digit(cell: str | None) -> bool:
    return cell is not None and any(ch.isdigit() for ch in cell)


def _is_ref_cell(cell: str, next_cell: str | None) -> bool:
    if cell in _LAYOUT_WORDS:
        return False
    if _NUMBERED_REF.fullmatch(cell):
        return True
    if cell in constants.BARE_DESIGNATORS:
        # 'LED' is also an ordinary value ('D3  LED'); as a designator it is
        # followed by a numeric value ('LED  3mm Red LED').
        return _has_digit(next_cell)
    if cell in constants.KEYWORDS:
        return True
    # Unfamiliar control names (SHIFT, SUBS) are recognized by their value.
    return (
        next_cell is not None
        and _CONTROL_NAME.fullmatch(cell) is not None
        and _CONTROL_VALUE.match(next_cell) is not None
    )


def _starts_listing(cell: str, next_cell: str | None) -> bool:
    """Whether a cell in value position is really the next designator."""
    if _BOARD_REF.fullmatch(cell):
        return True
    return cell in constants.BARE_DESIGNATORS and _has_digit(next_cell)


def _is_annotation(cell: str) -> bool:
    words = cell.split()
    return not (
        cell.startswith("*")
        or all(w.upper() in _LAYOUT_WORDS for w in words)
        or _BOARD_REF.fullmatch(words[0]) is not None
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
    return _REDUNDANT_SUFFIX.sub("", value)


def find_listings(words: list[PDFWord]) -> list[Listing]:
    """Finds every designator/value pair on a page.

    Args:
        words: Words extracted from one page.

    Returns:
        Listings in reading order (top to bottom, left to right).
    """
    listings: list[Listing] = []
    for line in group_lines(words):
        cells = split_cells(line)
        i = 0
        while i < len(cells):
            nxt = cells[i + 1] if i + 1 < len(cells) else None
            after = cells[i + 2] if i + 2 < len(cells) else None
            if (
                nxt is None
                or not _is_ref_cell(cells[i], nxt)
                or _starts_listing(nxt, after)
            ):
                i += 1
                continue
            parts = [nxt]
            j = i + 2
            while j < len(cells):
                following = cells[j + 1] if j + 1 < len(cells) else None
                if _is_ref_cell(cells[j], following) or not _is_annotation(cells[j]):
                    break
                parts.append(cells[j])
                j += 1
            value = clean_value(" ".join(parts))
            if value:
                listings.append(Listing(ref=cells[i], value=value))
            i = j
    return listings
