"""Where in a document each part was read.

The readers in `parser.py` take many routes from positioned words to parts
(ruled tables, text tables, column layouts, OCR), and none of them keeps
positions. Rather than thread a position through every route, each part is
found again afterwards: its designator is a word on the page, and the row it
was read from is the line holding that word and its value. A designator can
be printed several times (the parts list, the board's silkscreen, the
schematic), so a line that also prints the part's value wins, on the page
where the most designators print theirs.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache

from src.bom_lib.constants import INJECTED_SUFFIX
from src.bom_lib.designators import is_designator
from src.bom_lib.types import Inventory, Location, PDFPageExtraction, PDFWord
from src.bom_lib.utils import expand_refs, parse_value_to_decimal

# Words this close to each other's top (as a share of their size) share a line.
_LINE_SHARE = 0.5
# A range printed as separate words ('R1 - R4') spans at most this many.
_RANGE_WORDS = 3
# A repeated designator's label: 'R1 (2)' is the second R1 (another board).
_REPEAT = re.compile(r"^(.+) \((\d+)\)$")
# Designators starting within this many points share a column (OCR's boxes
# jitter by a point or two).
_COLUMN_PT = 3
# A word this long can match part of a value ('2N5089' of '2N5089A'); a
# shorter one only the whole value, or a stray '1' would match '10k'.
_PARTIAL_CHARS = 3
# Punctuation around a designator or value word ('R1,' '(10k)').
_PUNCTUATION = ",;:()[]"


@dataclass(frozen=True, slots=True)
class _Line:
    """Words printed on one line of a page, left to right."""

    page: int
    words: tuple[PDFWord, ...]


@dataclass(frozen=True, slots=True)
class _Mention:
    """A place a designator is printed: its words on a line, and its value's.

    Attributes:
        line: The line.
        start: The designator's first word.
        end: Just past the designator's last word.
        value_end: Just past the last word of its value, or `end`.
        matched: How many of the line's later words match the value.
    """

    line: _Line
    start: int
    end: int
    value_end: int
    matched: int


def _lines(page: int, words: Iterable[PDFWord]) -> list[_Line]:
    lines: list[list[PDFWord]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        last = lines[-1] if lines else None
        if last and abs(word["top"] - last[0]["top"]) <= _LINE_SHARE * word["size"]:
            last.append(word)
        else:
            lines.append([word])
    return [_Line(page, tuple(sorted(ws, key=lambda w: w["x0"]))) for ws in lines]


def _clean(text: str) -> str:
    return text.strip(_PUNCTUATION).upper()


def _names(words: Sequence[PDFWord], start: int, end: int) -> set[str]:
    """The designators a run of words names ('R1-R4', 'C1,' 'Q1 - Q2')."""
    text = " ".join(w["text"] for w in words[start:end]).strip(_PUNCTUATION)
    return {_clean(ref) for ref in expand_refs(text)}


@cache
def _number(text: str) -> Decimal | None:
    return parse_value_to_decimal(text)


def _same(printed: str, wanted: str) -> bool:
    """Whether a printed word is (part of) a value word, as text or as a number.

    The parts list prints a value as its publisher writes it, and the reading
    stores it normalized: '2K2' and '2.2k', '4u7' and '4.7u' are the same.
    """
    a, b = printed.casefold(), wanted.casefold()
    if a == b or (min(len(a), len(b)) >= _PARTIAL_CHARS and (a in b or b in a)):
        return True
    number = _number(printed)
    return number is not None and number == _number(wanted)


def _value_words(words: Sequence[PDFWord], after: int, value: str) -> tuple[int, int]:
    """Where the words after a designator that match its value end, and how many.

    The value is the first run of matching words ('3mm Red LED'): the rest of
    the line can be another column of parts, or a picture's labels beside the
    table. The next designator on the line starts another row, unless the
    designators are a list.
    """
    wanted = {t.strip(_PUNCTUATION) for t in value.split()} - {""}
    matched, end = 0, after
    for k in range(after, len(words)):
        word = words[k]["text"].strip(_PUNCTUATION)
        if word and any(_same(word, t) for t in wanted):
            matched += 1
            end = k + 1
        elif matched:
            break
        elif is_designator(word) and not words[k - 1]["text"].endswith(","):
            # A designator can be the value, though ('Q4  J201', 'D3  LED'),
            # and a list goes on to its value: 'D1, D2, D3  1N914'.
            break
    return end, matched


def _printed(lines: Iterable[_Line]) -> dict[str, list[tuple[_Line, int, int]]]:
    """Every designator printed on the lines, with the words that name it."""
    printed: dict[str, list[tuple[_Line, int, int]]] = {}
    for line in lines:
        words = line.words
        for start in range(len(words)):
            named: set[str] = set()
            for end in range(start + 1, min(start + _RANGE_WORDS, len(words)) + 1):
                for ref in _names(words, start, end) - named:
                    printed.setdefault(ref, []).append((line, start, end))
                    named.add(ref)
    return printed


def _column(mention: _Mention) -> tuple[int, int]:
    """The page and column a designator starts in, to the nearest few points."""
    return mention.line.page, round(
        mention.line.words[mention.start]["x0"] / _COLUMN_PT
    )


def _box(mention: _Mention) -> Location:
    words = mention.line.words[mention.start : max(mention.end, mention.value_end)]
    return Location(
        page=mention.line.page,
        x0=min(w["x0"] for w in words),
        top=min(w["top"] for w in words),
        x1=max(w["x1"] for w in words),
        bottom=max(w["top"] + w["size"] for w in words),
    )


def _designators(inventory: Inventory) -> dict[str, str]:
    """Each printed designator's value, in the order the parts were read."""
    return {
        ref: key.value
        for key, data in inventory.items()
        for ref in data["refs"]
        if not ref.endswith(INJECTED_SUFFIX)
    }


def locate(
    pages: Sequence[PDFPageExtraction],
    inventory: Inventory,
    page_numbers: Sequence[int] | None = None,
) -> dict[str, Location]:
    """Finds where each designator in a parts list was read.

    Args:
        pages: The pages the readers read, in order.
        inventory: The parts read from them (one build's, for a document with
            several).
        page_numbers: Each page's number in the document (0-based), when
            `pages` are only some of its pages (OCR reads only image pages);
            by default they are the whole document.

    Returns:
        Each found designator's place. A designator isn't found when it isn't
        printed as a word (a part read from a counted list, '4 - 10k'), and a
        repeat ('R1 (2)') takes the next mention its first didn't.
    """
    numbers = page_numbers if page_numbers is not None else range(len(pages))
    lines = [
        line
        for page, number in zip(pages, numbers, strict=True)
        for line in _lines(number, page["words"])
    ]
    printed = _printed(lines)
    # Each label's designator ('R1' for 'R1 (2)') and where it's printed.
    mentions: dict[str, tuple[str, list[_Mention]]] = {}
    for label, value in _designators(inventory).items():
        repeat = _REPEAT.match(label)
        ref = _clean(repeat[1] if repeat else label)
        mentions[label] = (
            ref,
            [
                _Mention(line, start, end, *_value_words(line.words, end, value))
                for line, start, end in printed.get(ref, [])
            ],
        )

    # The parts list is on the pages where the most designators print their
    # values, in the columns where they line up.
    listing: Counter[int] = Counter()
    columns: Counter[tuple[int, int]] = Counter()
    for _, found in mentions.values():
        listing.update({m.line.page for m in found if m.matched})
        columns.update({_column(m) for m in found if m.matched})

    def rank(m: _Mention) -> tuple[bool, int, int, int]:
        page, x = _column(m)
        aligned = sum(columns[page, x + dx] for dx in (-1, 0, 1))
        return m.matched > 0, listing[m.line.page], aligned, m.matched

    located: dict[str, Location] = {}
    # The mentions each designator's labels took: a repeat takes another.
    taken: set[tuple[str, int, int]] = set()
    for label, (ref, found) in mentions.items():
        free = [m for m in found if (ref, id(m.line), m.start) not in taken]
        if not free:
            continue
        best = max(free, key=rank)
        taken.add((ref, id(best.line), best.start))
        located[label] = _box(best)
    return located
