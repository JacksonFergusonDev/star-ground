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

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from src.bom_lib import constants
from src.bom_lib.designators import BOARD_REF, is_designator
from src.bom_lib.types import PDFWord
from src.bom_lib.utils import expand_refs

# Gaps wider than this fraction of the font size separate cells. Measured on
# PedalPCB documents: spaces inside a value are ~0.3em, column gaps >= 0.6em.
_CELL_GAP_EM = 0.45
# Words whose tops differ by less than this fraction of the font size share a
# line. Words on a real line differ by <= 0.05em; an adjacent footnote line in
# The Abyss sits only 0.19em away.
_LINE_TOLERANCE_EM = 0.15

# Unfamiliar numbered labels (CTRL1, FUZZ2) that are not standard designators.
_NUMBERED_LABEL = re.compile(r"[A-Z]{1,5}\d{1,3}")
# A designator range in a listing grid: 'D1-D2  1N4148' (GuitarPCB SWAH).
REF_RANGE = re.compile(r"([A-Z]{1,4}\d{1,3}) ?[-\u2013] ?(?:[A-Z]{1,4})?\d{1,3}")
# Single letters (Blue Waffle's 'V  B500K') and slashes ('BASS/MIDS') occur.
_CONTROL_NAME = re.compile(r"[A-Z](?:[A-Z0-9_/-]{1,11})?")
# A control name in title case, recognized only beside a tapered pot value:
# 'Volume  A100k', 'Mid  B100k' (GuitarPCB Tone TwEQ).
_TITLE_NAME = re.compile(r"[A-Z][a-z]{2,11}")
_TAPERED_POT = re.compile(r"[ABCWG]\d+(?:\.\d+)?[KkM]")
_MULTIWORD_NAME = re.compile(r"[A-Z]{2,}(?: [A-Z]{2,}){1,2}|[A-Z]{3,} \d{1,2}")
_NUMBERED_NAME_HEAD = re.compile(r"[A-Z]{3,}")
# A whole-cell parenthetical on the next line, aligned with a value above it,
# continues that value ('SPDT Toggle' / '(On/On)').
_CONTINUATION = re.compile(r"\(.*\)")
# A pot value or switch spec, optionally led by a marker or qualifier:
# 'Dual B10K', 'Latching 3PDT Footswitch', 'On/OFF/On DPDT Toggle', '*DPDT'.
_CONTROL_VALUE = re.compile(
    r"\*?(?:(?i:dual|latching|momentary|mini|slide|type-?2|on(?:\s*[/-]\s*(?:on|off))+)\s+)*"
    # Kilohms in either case: 'VOL  A100k' (GuitarPCB Blues Power).
    r"(?:[ABCWG]\d+(?:\.\d+)?[KkM][ABC]?\b|\d+(?:\.\d+)?[KkMR][ABC]?\b|[SD34]P[DS]T\b|\dP\dT\b"
    # A jack: 'SIDECHAIN  1/4" Switching TRS' (Muzzle).
    r"|(?=\S*\d.*\b(?i:trs|jack)\b)"
    # Toggle positions alone: 'SLA  ON/OFF/ON' (Trojan Horse).
    r"|(?i:\(?on(?:\s*[/-]\s*(?:on|off))+\)?)$)"
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
    "LEDS",
    "OPTOISOLATORS",
    "FOOTSWITCHES",
    "TUBES",
    "NUTUBE",
    "PIN",
    "HEADERS",
}
# A merged 'NAME value' cell is split only when the value is short
# ('DPDT (On / Off / On)' is six words).
_MAX_MERGED_VALUE_WORDS = 6
# A designator column is an x-position where at least this many listings start.
_MIN_COLUMN_REFS = 3
_COLUMN_TOLERANCE_EM = 0.5
# A part description in a type column: '1/4 Watt Resistor', 'Box Cap',
# 'Electrolytic Capacitor - Min 16V', 'NPN - EBC', '16mm Potentiometer'.
_TYPE_TEXT = re.compile(
    r"\b(?:resistor|cap(?:acitor)?|diode|pot(?:entiometer)?|transistor|mosfet|j-?fet"
    r"|[np]pn|op[- ]?amp|regulator|toggle|trim(?:-?pot|mer)?|footswitch|switching"
    # A type cell OCR splits in two keeps only its first word: 'Box | Cap'.
    r"|box|ceramic|electrolytic|polari[sz]ed|mlcc|film|schottky|zenn?er|germanium"
    r"|charge pump|darlington"
    r"|watt|standard|\d+[- ]?pin|dip-?\d*)\b",
    re.IGNORECASE,
)
_TYPE_SHARE = 0.6
# A part value or part number: '33k', '2M2', '220uF', 'B5K', 'TL072', '1N4148'.
_VALUE_SHAPE = re.compile(
    r"[ABCW]?\d+(?:\.\d+)?[pnuµkKMR]\d*(?:F|Ω)?|\d?[A-Za-z]{1,4}\d{2,}[A-Z]*",
)
_TRIM_HEADING = re.compile(r"TRIM(?:MER)?S?(?:\s+POTS?)?\b", re.IGNORECASE)
_TRIM_MARK = re.compile(r"\bTRIM|\b3362P?\b", re.IGNORECASE)
_PAREN_LABEL = re.compile(r"\((\w+)\)")
_DASHES = frozenset({"-", "\u2013", "\u2014"})
# A designator set with a space ('SW 1', EA Tremolo) ends the value before it.
_SPACED_REF = re.compile(r"(?:SW|IC|LED|Q|D|C|R) \d{1,3}")
# A function label printed between a designator and its value: 'SW1 (Power) SPDT'.
_LABEL = re.compile(r"\([^()\d]*\)")
# The number of a control name set apart from it: 'CRACKLE | 1 | C5K'.
_NAME_NUMBER = re.compile(r"\d{1,2}")
_LED_WORD = re.compile(r"\bLED\b", re.IGNORECASE)
# An LED's size, which starts its description: '3MM LED', '3mm Red LED'.
_LED_SIZE = re.compile(r"\d+(?:[./]\d+)?\s*mm\b", re.IGNORECASE)
# Two or more lowercase words mark a sentence ('Choose between ... or SMD').
_LOWERCASE_WORD = re.compile(r"[a-z][a-z'-]*[.,;:]?")
_ASIDE = re.compile(r"\((?:see (?:notes?|text)|or equivalent)\)", re.IGNORECASE)
# Trailing words that only restate the component type.
# A part supplied with the PCB is still a part: '24LC32A *Included' (Deflector).
_INCLUDED = re.compile(r"\s*\*\s*Included(?: with (?:the )?PCB)?\s*$", re.IGNORECASE)
# 'Dip Switch' stays: a bare 'DIP2' would read as an IC package size.
_REDUNDANT_SUFFIX = re.compile(r"(?<!\bDIP)\s+(?:Crystal|Switch)$", re.IGNORECASE)


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
    # An optional-part marker is not part of the name: 'TREBLE*' (Fuchsia).
    cell = _unmarked(cell)
    if cell in _LAYOUT_WORDS:
        return False
    if cell in constants.BARE_DESIGNATORS:
        return _is_bare_value(next_cell)
    if is_designator(cell) or _NUMBERED_LABEL.fullmatch(cell):
        return True
    span = REF_RANGE.fullmatch(cell)
    if span and is_designator(span.group(1)):
        # Not before a bare count: 'D21 - D28  8' is a quantity column (Q-Tune).
        return next_cell is not None and not next_cell.isdigit()
    if (
        _TITLE_NAME.fullmatch(cell)
        and next_cell is not None
        and _TAPERED_POT.fullmatch(next_cell)
    ):
        return True
    if cell in constants.KEYWORDS:
        return True
    # Unfamiliar control names (SHIFT, SUBS, TONE BYPASS SWITCH) are recognized
    # by their value.
    named = (
        _CONTROL_NAME.fullmatch(cell) is not None
        # A pole/throw code is a value: 'DPDT  On-On-On' (Minidrive).
        and constants.SWITCH_VALUE_PATTERN.fullmatch(cell) is None
    ) or (
        _MULTIWORD_NAME.fullmatch(cell) is not None
        and not set(cell.split()) <= _HEADER_WORDS  # 'TOGGLE SWITCHES' is a header
    )
    return (
        next_cell is not None and named and _CONTROL_VALUE.match(next_cell) is not None
    )


def _unmarked(cell: str) -> str:
    """A designator without its see-text asterisks ('TREBLE*', '**D3')."""
    return cell.strip("*") or cell


def _ref_name(cell: str) -> str:
    """The designator a ref cell names; control names are upper case ('Volume')."""
    name = _unmarked(cell)
    return name.upper() if _TITLE_NAME.fullmatch(name) else name


def _starts_listing(cell: str, next_cell: str | None) -> bool:
    """Whether a cell in value position is really the next designator."""
    if BOARD_REF.fullmatch(cell):
        return True
    span = REF_RANGE.fullmatch(cell)
    if span and is_designator(span.group(1)):
        return True
    # Directly after a designator a bare 'LED' is that designator's value
    # ('LED_A  LED  Indicator LED'), unless a numeric value follows it; a
    # size describes the LED ('D5  LED  3MM LED', Five Cats).
    return (
        cell in constants.BARE_DESIGNATORS
        and _has_digit(next_cell)
        and not (cell == "LED" and _LED_SIZE.match(next_cell or ""))
    )


def _is_bare_value(cell: str | None) -> bool:
    """Whether a cell can be the value of a bare designator ('LED', 'CLR').

    'LED' is also an ordinary value ('D3  LED'); as a designator it is followed
    by a numeric value ('LED  3mm Red LED') or an LED description that does
    not repeat it on its own ('LED  Indicator LED').
    """
    if cell is None or _NUMBERED_LABEL.fullmatch(cell):
        # 'LED  POT1  B100K': the next designator is no value (Five Cats ZTrem).
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


def strip_asides(value: str) -> str:
    """Removes '(see notes)', '(see text)' and '(or equivalent)' from a value."""
    return " ".join(_ASIDE.sub("", value).split()) or value


def clean_value(value: str) -> str:
    """Strips markers that are not part of a purchasable value.

    Removes optional-part asterisks, '(see notes)' and '(or equivalent)' asides,
    '*Included' markers, and trailing words that only restate the component
    type ('AB38T Crystal' -> 'AB38T').

    Args:
        value: The raw value text.

    Returns:
        The cleaned value.
    """
    value = _INCLUDED.sub("", _ASIDE.sub("", value)).replace("*", " ")
    value = " ".join(value.split())
    # A trailing link or remark is not the value: 'Mini 1P8T - https://...',
    # 'SPDT (On/On) - Short shaft toggle recommended' (Mini Heterodyne).
    head, dash, tail = value.rpartition(" - ")
    if dash and head and (tail.startswith("http") or _is_prose(tail)):
        value = head
    # A leading function label is not part of the value: '(Voltage) SPDT Toggle'.
    label = _LABEL.match(value)
    if label and label.end() < len(value):
        value = value[label.end() :].strip()
    return _REDUNDANT_SUFFIX.sub("", value)


def _join_numbered_names(cells: list[Cell]) -> list[Cell]:
    """Rejoins a control name and its number set a cell gap apart.

    OCR reads 'CRACKLE 1 | C5K' (Five Cats Double SHO-MON) as 'CRACKLE | 1 |
    C5K': without its number the name is no listing, and the part vanishes.
    """
    joined: list[Cell] = []
    for k, cell in enumerate(cells):
        nxt = cells[k + 1].text if k + 1 < len(cells) else None
        if (
            joined
            and _NAME_NUMBER.fullmatch(cell.text)
            and _NUMBERED_NAME_HEAD.fullmatch(joined[-1].text) is not None
            and nxt is not None
            and _CONTROL_VALUE.match(nxt) is not None
        ):
            joined[-1] = Cell(joined[-1].words + cell.words)
        else:
            joined.append(cell)
    return joined


def _split_merged(cell: Cell) -> list[Cell]:
    """Splits a 'NAME value' cell that a narrow gap failed to separate.

    In tab-aligned columns a long control name can end within an ordinary
    word space of its value ('BLEND B10K'), so both land in one cell. Such a
    cell is split when its first word is a designator or control name and the
    rest is a short value; prose ('D8 and D9 are only needed...') is left whole.
    """
    if REF_RANGE.fullmatch(cell.text):
        return [cell]  # 'Q1 -Q4' is one range (Dr Phybes)
    words = cell.text.split()
    dashed = False
    paren = _PAREN_LABEL.fullmatch(words[1]) if len(words) > 2 else None
    if paren and is_designator(paren.group(1)):
        # 'RANGE (SW1) - Mini Slide SPDT' (Muzzle): the board designator is the ref.
        cell = Cell(({**cell.words[1], "text": paren.group(1)}, *cell.words[2:]))
        words = cell.text.split()
    elif paren and words[2] in _DASHES:
        # 'SIDECHAIN (Key) - 1/4" Switching TRS': a function label, then the value.
        cell = Cell((cell.words[0], *cell.words[3:]))
        words = cell.text.split()
        dashed = True
    if (
        len(words) > 2
        and words[1] in _DASHES
        # Not a range: 'D21 - D28' (Q-Tune), 'POT1 - 6' (6 Band EQ).
        and not (words[2].isdigit() or is_designator(words[2]))
    ):
        # 'MODE - 1P8T Mini Rotary' (HAARP): a dash separates name and value.
        cell = Cell((cell.words[0], *cell.words[2:]))
        words = cell.text.split()
        dashed = True
    if len(words) < 2 or len(words) > _MAX_MERGED_VALUE_WORDS + 1:
        return [cell]
    name = words[0]
    if constants.SWITCH_VALUE_PATTERN.fullmatch(name):
        return [cell]  # 'DPDT On-On-On' is one value (Minidrive)
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
        _CONTROL_NAME.fullmatch(name) and (dashed or _CONTROL_VALUE.match(rest))
    ):
        return split
    return [cell]


# A footnote marker is a short number set at most this fraction of the size of
# the word it follows (Aion FX legacy: '68k' at 11pt, marker '1' at 6.4pt).
_SUPERSCRIPT_RATIO = 0.75


def footnote_marks(words: list[PDFWord]) -> list[PDFWord]:
    """Superscript footnote markers printed right after a word.

    'R2  68k¹' and 'IC1  LT1054CP⁵' would otherwise read as values '68k 1'
    and 'LT1054CP 5' (Aion FX legacy documents).

    Args:
        words: Words extracted from one page.

    Returns:
        The marker words, so the caller can drop their characters.
    """
    return [
        w
        for w in words
        if w["text"].isdigit()
        and len(w["text"]) <= 2
        and any(
            v is not w
            and w["size"] <= _SUPERSCRIPT_RATIO * v["size"]
            and 0 <= w["x0"] - v["x1"] <= v["size"]
            and abs((v["top"] + v["size"]) - (w["top"] + w["size"])) < v["size"]
            for v in words
        )
    ]


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
    # A first pass finds where designator columns start, so a value does not
    # absorb text that sits in the next column ('C24  10n | 6 x 5-pin Male
    # Pinheader' under a PIN HEADERS heading, Emu Amp Simulator).
    lines = page_lines(words)
    starts = Counter(round(listing.x0) for listing in _scan(words, frozenset()))
    columns = frozenset(x for x, n in starts.items() if n >= _MIN_COLUMN_REFS)
    return _mark_trimmers(_scan(words, columns, _type_columns(lines)), lines)


def starts_description(value: str, text: str) -> bool:
    """Whether text after a value is the part's description, in its own cell.

    OCR can set a 'Value | Type' pair closer than a cell gap, or read it as
    one box: 'TL072 Dual Op Amp', 'B250K Potentiometer', '1N914  Small
    Signal, Fast Switching Diode'.
    """
    return (
        _VALUE_SHAPE.fullmatch(value) is not None
        and bool(text)
        and _describes(text, None)
    )


def _type_columns(lines: list[list[Cell]]) -> frozenset[int]:
    """Where a column of part descriptions starts.

    A 'Location | Value | Type' table without its header row (Five Cats, read
    by OCR) would otherwise run each value into its description: '33k 1/4
    Watt Resistor'. A column is a type column when most of its cells describe
    a kind of part.
    """
    # OCR can split a description into a cell per word ('8 | Pin | Dual
    # Op-Amp'), so a cell is read with the one after it.
    described = {
        id(cell): " ".join(c.text for c in cells[k : k + 2])
        for cells in lines
        for k, cell in enumerate(cells)
        if k
    }
    cells = [cell for cells in lines for cell in cells[1:]]

    def describes(cell: Cell) -> bool:
        following = described[id(cell)][len(cell.text) + 1 :] or None
        return _describes(cell.text, following)

    typed = [cell for cell in cells if describes(cell)]
    columns = set()
    for cell in typed:
        near = [
            c for c in cells if abs(c.x0 - cell.x0) <= _COLUMN_TOLERANCE_EM * c.size
        ]
        kinds = sum(1 for c in near if describes(c))
        if kinds >= _MIN_COLUMN_REFS and kinds >= _TYPE_SHARE * len(near):
            columns.add(round(cell.x0))
    return frozenset(columns)


def _describes(cell: str, next_cell: str | None) -> bool:
    """Whether a cell (read with the next) describes a kind of part.

    A value is never a description, though the cell after it may be one.
    """
    text = cell if next_cell is None else f"{cell} {next_cell}"
    return (
        _TYPE_TEXT.search(text) is not None
        and _VALUE_SHAPE.fullmatch(cell.split()[0]) is None
    )


def _mark_trimmers(listings: list[Listing], lines: list[list[Cell]]) -> list[Listing]:
    """Marks listings under a 'TRIM POTS' heading in their column as trimmers.

    CDXL Reissue lists trimmers R20 and R23 under 'TRIM POTS (3362P Type)';
    by designator alone they would be bought as fixed resistors.
    """
    refs = {(listing.line, round(listing.x0)) for listing in listings}
    headings = [
        (line_no, cell.x0, cell.size, _TRIM_HEADING.match(cell.text) is not None)
        for line_no, cells in enumerate(lines)
        for cell in cells
        if cell.text.split()[0].upper() in _HEADER_WORDS
        and (line_no, round(cell.x0)) not in refs
    ]
    marked = []
    for listing in listings:
        above = [
            trim
            for line_no, x0, size, trim in headings
            if line_no < listing.line
            and abs(x0 - listing.x0) <= _COLUMN_TOLERANCE_EM * size
        ]
        if above and above[-1] and not _TRIM_MARK.search(listing.value):
            listing = Listing(
                listing.ref, f"{listing.value} TRIM", listing.line, listing.x0
            )
        marked.append(listing)
    return marked


def _in_column(x0: float, size: float, columns: frozenset[int]) -> bool:
    return any(abs(x0 - x) <= _COLUMN_TOLERANCE_EM * size for x in columns)


def _scan(
    words: list[PDFWord],
    designators: frozenset[int],
    types: frozenset[int] = frozenset(),
) -> list[Listing]:
    """One listing pass; annotations never extend into another column.

    On a page with type columns, a part description ('6mm Trim Pot') ends a
    value and names no part itself, so it starts no listing up to the next
    designator.
    """
    columns = designators | types
    # A column where both designators and descriptions start is not skipped.
    only_types = types - designators
    listings: list[Listing] = []
    # (value x-position, font size, index into listings) for the previous line.
    above: list[tuple[float, float, int]] = []
    for line_no, line in enumerate(group_lines(words)):
        cells = _join_numbered_names(
            [part for cell in split_cells(line) for part in _split_merged(cell)]
        )
        cells = _absorb_continuations(cells, above, listings)
        texts = [c.text for c in cells]
        current: list[tuple[float, float, int]] = []
        i = 0
        while i < len(texts):
            if _in_column(cells[i].x0, cells[i].size, only_types):
                i = _description_end(cells, i, designators)
                continue
            nxt = texts[i + 1] if i + 1 < len(texts) else None
            after = texts[i + 2] if i + 2 < len(texts) else None
            if nxt is None and current and BOARD_REF.fullmatch(texts[i]):
                # SuperNuva lists 'D1' and 'D2' with no value at the end of a
                # line of listings: a part the builder cannot buy as printed.
                listings.append(
                    Listing(ref=texts[i], value="", line=line_no, x0=cells[i].x0)
                )
                i += 1
                continue
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
                # Only a value that already has its part stops at the next
                # column: 'SW1  (FM/CC/Vintage)  DPDT On-On-On' (Minidrive) runs
                # its value under the pots column.
                complete = not _LABEL.fullmatch(" ".join(parts))
                if types and (
                    (parts == ["LED"] and _LED_SIZE.match(texts[j]))
                    or (
                        _VALUE_SHAPE.fullmatch(parts[0]) is not None
                        and _describes(texts[j], following)
                    )
                ):
                    # The value's description: 'D5 | LED | 3MM LED',
                    # 'U2 | TL072 | 8 Pin Dual Op-Amp'.
                    break
                if (
                    _is_ref_cell(texts[j], following)
                    or _SPACED_REF.fullmatch(texts[j])
                    or not _is_annotation(texts[j])
                    or (complete and _in_column(cells[j].x0, cells[j].size, columns))
                ):
                    break
                parts.append(texts[j])
                j += 1
            value = clean_value(" ".join(parts))
            if value:
                current.append((cells[i + 1].x0, cells[i + 1].size, len(listings)))
                listings.append(
                    Listing(
                        ref=_ref_name(texts[i]),
                        value=value,
                        line=line_no,
                        x0=cells[i].x0,
                    )
                )
            following = _text_at(texts, j + 1)
            if (
                types
                and j < len(texts)
                # 'D1 | 5v1 Zener' after a value is the next listing (GuitarPCB
                # Dr-Phybes).
                and not _is_ref_cell(texts[j], following)
                and _describes(texts[j], following)
            ):
                # Its description names no part: '5K | 6mm Trim Pot'.
                j = _description_end(cells, j, designators)
            i = j
        above = current
    return listings


def _text_at(texts: list[str], k: int) -> str | None:
    return texts[k] if k < len(texts) else None


def _description_end(cells: list[Cell], start: int, designators: frozenset[int]) -> int:
    """Where a part description starting at a cell ends: the next designator."""
    k = start + 1
    # A numbered designator or a control name: a description's 'Trim Pot' or
    # 'LED' is none.
    while k < len(cells) and not (
        BOARD_REF.fullmatch(cells[k].text)
        or _NUMBERED_LABEL.fullmatch(cells[k].text)
        or (
            cells[k].text.isupper()
            and _is_ref_cell(cells[k].text, _text_at([c.text for c in cells], k + 1))
        )
        or _in_column(cells[k].x0, cells[k].size, designators)
    ):
        k += 1
    return k


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


# Column headers of a text bill of materials ('Reference  Value  Quantity  Notes',
# Aion FX's 'PART  VALUE  TYPE  NOTES').
_REF_HEADERS = frozenset(
    {"REFERENCE", "REFERENCES", "REF", "REFS", "DESIGNATOR", "PART", "LOCATION"}
)
# 'Name' heads the designator column only beside a 'Value' column (Q-Tune's
# 'Value | Name | Qty' packing list).
_NAME_HEADER = "NAME"
_BOM_COLUMNS = {
    "VALUE": "value",
    "QUANTITY": "qty",
    "QTY": "qty",
    "QT": "qty",  # 'Qt' / 'y' wrapped in a narrow column (Q-Tune)
    "NOTES": "notes",
    "COMMENTS": "notes",
    "TYPE": "type",
    "FOOTPRINT": "type",
}
# A cell starting this many ems right of the last header is outside the table.
_BEYOND_TABLE_EM = 4.0
# A wrapped value loses its closing parenthesis: '1N5226 (1N5' / '226)'.
_UNCLOSED_ASIDE = re.compile(r"\s*\([^)]*$")
# A part's printed marking is not its value: '100n (104)', '1N4148 (41 48)',
# '100n (.100J63)'. Words in parentheses ('(low ESR)', '(KY5050)') stay.
_MARKING = re.compile(r"\s*\(\s*(\.?\d[\d .]*(?:[JKM]\d+)?)\s*\)$")
# Separators inside a printed designator list: 'R11, R13', 'D21 - D28'.
_DESIGNATOR_SHAPE = re.compile(r"[A-Za-z]{1,6}\d*[A-Za-z]{0,3}")
_LIST_SPACING = re.compile(r"\s*([,-])\s*")


@dataclass(frozen=True, slots=True)
class BomRow:
    """One row of a text bill of materials with grouped designators.

    Attributes:
        refs: The designator cell, separators unspaced ('C4,C5', 'R10-R12,R18').
        value: The value cell.
        quantity: The quantity cell, if the table has one and it is a number.
        line: Index of the visual line on the page.
        x0: Left edge of the designator cell, in points.
        marking: The part marking printed after the value ('104' for
            '100n (104)'), if any.
        type_text: The TYPE cell, if the table has one ('Metal film resistor, 1/4W').
        notes: The NOTES cell, including lines that continue it below the row.
        table: Which header line on the page the row sits under, counting from 0
            (a page can hold two boards' tables, GuitarPCB Tweed Fuzztone).
        end: Left edge of a headerless table beside the row, whose listings
            are not part of it; infinite when there is none.
    """

    refs: str
    value: str
    quantity: int | None
    line: int
    x0: float
    marking: str = ""
    type_text: str = ""
    notes: str = ""
    table: int = 0
    end: float = math.inf


@dataclass(frozen=True, slots=True)
class _Column:
    field: str
    x0: float
    x1: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def heads_typed_table(words: list[PDFWord]) -> bool:
    """Whether a line of words heads a typed table ('Ref | Value | Footprint')."""
    names = {w["text"].upper() for w in words}
    types = {name for name, field in _BOM_COLUMNS.items() if field == "type"}
    return bool(names & _REF_HEADERS) and "VALUE" in names and bool(names & types)


def _header_sections(cells: list[Cell]) -> list[list[_Column]]:
    """The 'Reference | Value | Quantity' header groups on one line, left to right.

    A line can carry several tables side by side (Q-Tune: 'Value Name Qty
    Value Name Qty'); a repeated field starts the next group.
    """
    has_value = any(c.text.upper() == "VALUE" for c in cells)
    sections: list[list[_Column]] = []
    current: list[_Column] = []
    for cell in cells:
        name = cell.text.upper()
        if name in _REF_HEADERS or (name == _NAME_HEADER and has_value):
            field_name = "refs"
        elif name in _BOM_COLUMNS:
            field_name = _BOM_COLUMNS[name]
        else:
            continue
        if any(col.field == field_name for col in current):
            sections.append(current)
            current = []
        current.append(_Column(field_name, cell.x0, cell.words[-1]["x1"]))
    sections.append(current)
    return [sec for sec in sections if {"refs", "value"} <= {col.field for col in sec}]


def _column_for(cell: Cell, sections: list[list[_Column]]) -> tuple[int, str]:
    """The section and field a cell belongs to.

    A cell that starts at a header belongs to it (left-aligned tables); any
    other cell goes to the header nearest its center (Q-Tune centers its cells).
    """
    center = (cell.x0 + cell.words[-1]["x1"]) / 2
    tolerance = _LINE_TOLERANCE_EM * cell.size
    columns = [(k, col) for k, sec in enumerate(sections) for col in sec]
    for k, col in columns:
        if abs(cell.x0 - col.x0) <= tolerance:
            return k, col.field
    k, col = min(columns, key=lambda kc: abs(center - kc[1].center))
    return k, col.field


def find_bom_rows(words: list[PDFWord]) -> list[BomRow]:
    """Reads text tables headed 'Reference | Value | Quantity | Notes'.

    Unlike a ruled table, nothing but alignment marks the columns, and one row
    lists several designators (Sheepylove: 'R10-R12,R18  47k  4'). Several
    tables may sit side by side under one header line, and a later header
    line replaces only the tables it overlaps. Rows whose designator cell is
    not a list of designators (titles, footers, hardware) are skipped.

    Args:
        words: Words extracted from one page.

    Returns:
        The rows under every such header on the page, top to bottom.
    """
    return _read_bom(words)[0]


def bom_note_cells(words: list[PDFWord]) -> list[Cell]:
    """The NOTES cells of typed text tables on a page.

    Designators mentioned in a note ('Mistakenly labeled C19 on initial
    release', Aion FX Fusion) are not listings, so the self-check that looks
    for unextracted designators must not read them.
    """
    return _read_bom(words)[1]


def _read_bom(words: list[PDFWord]) -> tuple[list[BomRow], list[Cell]]:
    rows: list[BomRow] = []
    note_cells: list[Cell] = []
    sections: list[list[_Column]] = []
    # Per table section, the index in `rows` of its latest row.
    last_row: dict[int, int] = {}
    header_no = -1
    for line_no, cells in enumerate(page_lines(words)):
        found = _header_sections(cells)
        if found:
            lo = min(col.x0 for sec in found for col in sec)
            hi = max(col.x1 for sec in found for col in sec)
            kept = [
                sec
                for sec in sections
                if max(c.x1 for c in sec) < lo or min(c.x0 for c in sec) > hi
            ]
            sections = sorted(kept + found, key=lambda sec: sec[0].x0)
            last_row = {}
            header_no += 1
            # The rest of the line is a row of the tables beside the new header.
            cells = [c for c in cells if c.x0 < lo or c.x0 > hi]
        if not sections:
            continue
        fields: dict[tuple[int, str], str] = {}
        first_x0: dict[int, float] = {}
        end = math.inf
        right = max(col.x1 for sec in sections for col in sec)
        for n, cell in enumerate(cells):
            if cell.x0 > right + _BEYOND_TABLE_EM * cell.size and _is_prose(cell.text):
                # Prose beside the table (K-Buffer's description) is no cell,
                # and listings there are another table's.
                end = cell.x0
                break
            target = _column_for(cell, sections)
            next_text = cells[n + 1].text if n + 1 < len(cells) else None
            first, _, rest = cell.text.partition(" ")
            if (
                target[1] == "type"
                and target in fields
                # OCR can read a listing as one box: 'FREQ B5K'.
                and _is_ref_cell(first, rest or next_text)
            ):
                # A listing after the TYPE cell starts a second table whose
                # header was not read (Five Cats prints two 'Location | Value |
                # Type' tables side by side; OCR keeps only one header).
                end = cell.x0
                break
            fields[target] = f"{fields.get(target, '')} {cell.text}".strip()
            if target[1] == "refs":
                first_x0.setdefault(target[0], cell.x0)
            if target[1] == "notes" and any(
                col.field == "type" for col in sections[target[0]]
            ):
                note_cells.append(cell)
        for k, section in enumerate(sections):
            typed = any(col.field == "type" for col in section)
            refs = _LIST_SPACING.sub(r"\1", fields.get((k, "refs"), ""))
            # GuitarPCB: '* CLR x1', 'CLR x 2', '*DRIVE', 'ORDER switch'.
            refs = " ".join(refs.replace("*", " ").split())
            count = _REF_COUNT.search(refs)
            if count:
                refs = refs[: count.start()]
            refs = _LABEL_NOUN.sub("", refs)
            value = _UNCLOSED_ASIDE.sub(
                "", _ASIDE.sub("", fields.get((k, "value"), ""))
            )
            notes = fields.get((k, "notes"), "")
            last = last_row.get(k)
            if typed and not refs and notes and last is not None:
                # A NOTES cell wrapped onto the next line, under its row.
                rows[last] = replace(rows[last], notes=f"{rows[last].notes} {notes}")
                continue
            marking = _MARKING.search(value)
            if marking:
                value = value[: marking.start()]
            # A typed table's TYPE explains any label; an untyped grid's label
            # ('Part | Value' grids: 'VOL | A100k') is judged by its value later.
            labeled = _is_label(refs) and (not typed or fields.get((k, "type")))
            if not value or not (_is_ref_list(refs) or labeled):
                continue
            # Optional-part markers are not part of the value ('*100p', '330k*').
            value = " ".join(value.replace("*", " ").split()) or value
            qty = fields.get((k, "qty"), "") or (count.group(1) if count else "")
            last_row[k] = len(rows)
            rows.append(
                BomRow(
                    refs=refs,
                    value=value,
                    quantity=int(qty) if qty.isdigit() else None,
                    line=line_no,
                    x0=first_x0[k],
                    marking=marking.group(1) if marking else "",
                    type_text=fields.get((k, "type"), ""),
                    notes=notes,
                    table=header_no,
                    end=end,
                )
            )
    return rows, note_cells


# 'CLR x1', 'CLR x 2': how many of the part to buy.
_REF_COUNT = re.compile(r"\s+x\s*(\d+)$")
# 'ORDER switch', 'DEEP switch': the noun restates the category.
_LABEL_NOUN = re.compile(r"(?<=[A-Z0-9]) (?:switch|pot|trimmer)$")
# A part label in a typed table: 'VOL.', 'HI-MID FREQ', 'IC1-S', 'PRE-V. SEND'.
_PART_LABEL = re.compile(r"[A-Z][A-Za-z0-9_./+-]*(?: [A-Za-z0-9_./+-]+){0,2}")


def _is_label(cell: str) -> bool:
    """Whether a typed table's PART cell names a part (any short label).

    A table with a TYPE column describes every row, so a row is a part even
    when its label is not a standard designator (Aion FX: 'FSW | 3PDT | Stomp
    switch'). The caller still requires the TYPE to name a category.
    """
    return bool(_PART_LABEL.fullmatch(cell))


def _is_ref_list(cell: str) -> bool:
    """Whether a cell lists designators.

    One odd name among real designators ('R8, RPDo' in Q-Tune) still makes a
    list, so the row is read and the odd name can be reported.
    """
    if not cell or " " in cell:
        return False
    refs = expand_refs(cell)
    return any(is_designator(ref) for ref in refs) and all(
        _DESIGNATOR_SHAPE.fullmatch(ref) for ref in refs
    )
