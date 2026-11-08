"""Finds 'R1 10k' listings in a page's words, however they are laid out.

Listings run in columns, wrap onto the next line, share a line with a
description, or come glued together ('R1-10k'); the scan pairs each
designator with its value.
"""

import re
from collections import Counter
from dataclasses import replace

from star_ground.bom_lib import constants
from star_ground.bom_lib.designators import BOARD_REF, is_designator
from star_ground.bom_lib.pdf.cells import (
    CONTROL_NAME,
    CONTROL_VALUE,
    HEADER_WORDS,
    KIND_SUFFIX,
    LABEL,
    NAMED_DESIGNATOR,
    NUMBERED_LABEL,
    REF_RANGE,
    TAPERED_POT,
    TITLE_NAME,
    VALUE_SHAPE,
    clean_value,
    describes,
    has_digit,
    is_annotation,
    is_ref_cell,
    names_control,
    ref_group,
    unmarked,
)
from star_ground.bom_lib.pdf.counted import COUNTED_ROW, counted_rows
from star_ground.bom_lib.pdf.geometry import (
    LINE_TOLERANCE_EM,
    Cell,
    Listing,
    group_lines,
    page_lines,
    split_cells,
)
from star_ground.bom_lib.types import PDFWord

_NUMBERED_NAME_HEAD = re.compile(r"[A-Z]{3,}")
# A whole-cell parenthetical on the next line, aligned with a value above it,
# continues that value ('SPDT Toggle' / '(On/On)').
_CONTINUATION = re.compile(r"\(.*\)")


# A merged 'NAME value' cell is split only when the value is short
# ('DPDT (On / Off / On)' is six words).
_MAX_MERGED_VALUE_WORDS = 6
# A designator column is an x-position where at least this many listings start.
_MIN_COLUMN_REFS = 3
_COLUMN_TOLERANCE_EM = 0.5


_TYPE_SHARE = 0.6


_TRIM_HEADING = re.compile(r"TRIM(?:MER)?S?(?:\s+POTS?)?\b", re.IGNORECASE)
_TRIM_MARK = re.compile(r"\bTRIM|\b3362P?\b", re.IGNORECASE)
_PAREN_LABEL = re.compile(r"\((\w+)\)")
_DASHES = frozenset({"-", "\u2013", "\u2014"})
# A designator set with a space ('SW 1', EA Tremolo) ends the value before it.
_SPACED_REF = re.compile(r"(?:SW|IC|LED|Q|D|C|R) \d{1,3}")


# The number of a control name set apart from it: 'CRACKLE | 1 | C5K'.
_NAME_NUMBER = re.compile(r"\d{1,2}")


# An LED's size, which starts its description: '3MM LED', '3mm Red LED'.
_LED_SIZE = re.compile(r"\d+(?:[./]\d+)?\s*mm\b", re.IGNORECASE)


def _ref_name(cell: str) -> str:
    """The designator a ref cell names; control names are upper case ('Volume')."""
    name = unmarked(cell)
    kind = KIND_SUFFIX.fullmatch(name)
    if kind:
        name = kind.group(1)
    if name.startswith("SW "):
        name = name.replace(" ", "")  # 'SW 1' (GuitarPCB EA Tremelo)
    named = NAMED_DESIGNATOR.fullmatch(name)
    if named and "=" in name and is_designator(named.group(1)):
        return named.group(1)  # 'P1=FUZZ' (GuitarPCB PUFF v4)
    group = ref_group(name)
    if group is not None:
        return group
    return name.upper() if TITLE_NAME.fullmatch(name) else name


def _starts_listing(cell: str, next_cell: str | None) -> bool:
    """Whether a cell in value position is really the next designator."""
    if BOARD_REF.fullmatch(cell) or ref_group(cell) is not None:
        return True
    span = REF_RANGE.fullmatch(cell)
    if span and is_designator(span.group(1)):
        return True
    if (
        TITLE_NAME.fullmatch(cell)
        and next_cell is not None
        and TAPERED_POT.fullmatch(next_cell)
    ):
        # 'R10 | 1k5 | Volume | 100k Log': the next row of a side-by-side table.
        return True
    # Directly after a designator a bare 'LED' is that designator's value
    # ('LED_A  LED  Indicator LED'), unless a numeric value follows it; a
    # size describes the LED ('D5  LED  3MM LED', Five Cats).
    return (
        cell in constants.BARE_DESIGNATORS
        and has_digit(next_cell)
        and not (cell == "LED" and _LED_SIZE.match(next_cell or ""))
    )


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
            and CONTROL_VALUE.match(nxt) is not None
        ):
            joined[-1] = Cell(joined[-1].words + cell.words)
        else:
            joined.append(cell)
    return joined


def _retext(word: PDFWord, text: str, x1: float | None = None) -> PDFWord:
    """A copy of a word with other text (and, for a joined range, another end)."""
    return PDFWord(
        text=text,
        x0=word["x0"],
        x1=word["x1"] if x1 is None else x1,
        top=word["top"],
        size=word["size"],
    )


# A designator with its listing's dash set against it: 'Q1-'.
_GLUED_DASH = re.compile(r"(.+?)[-\u2013]")


def _dash_listing(cell: Cell) -> list[Cell] | None:
    """Splits a 'designators - value' cell (Tonepad) into designators and value.

    The designators are one ('R7 - 220'), a comma list ('R2, R7, R18, R20 -
    47k', 'R11,R12 - 220k') or a range ('D1 - D5 - Silicon diode'). A bare
    'R7 - 220' would otherwise read as a range, and a long value as prose.
    """
    words = tuple(
        _retext(w, part)
        for w in cell.words
        for part in re.findall(r"[^,]+,?", w["text"]) or [w["text"]]
    )
    glued = _GLUED_DASH.fullmatch(words[0]["text"]) if len(words) > 1 else None
    if glued and _listed_ref(glued.group(1)):
        # 'Q1- 2N3904' (Tonepad Filter/Sample/Hold): the dash set against it.
        words = (_retext(words[0], glued.group(1)), _retext(words[0], "-"), *words[1:])
    texts = [w["text"] for w in words]
    if (
        len(texts) > 4
        and is_designator(texts[0])
        and texts[1] in _DASHES
        and is_designator(texts[2])
        and texts[3] in _DASHES
    ):
        span = _retext(words[0], f"{texts[0]}-{texts[2]}", x1=words[2]["x1"])
        return [Cell((span,)), Cell(words[4:])]
    k = 0
    while k < len(texts) - 1 and texts[k].endswith(","):
        if not _listed_ref(texts[k].rstrip(",")):
            return None
        k += 1
    if k + 2 >= len(texts) or texts[k + 1] not in _DASHES:
        return None
    if not _listed_ref(texts[k]) or is_designator(texts[k + 2]):
        return None  # 'D21 - D28' is a range
    if not k and texts[k + 2].isdigit() and len(texts[k + 2]) < 3 and len(texts) == 3:
        return None  # 'POT1 - 6' is a range
    refs = [_retext(w, w["text"].rstrip(",")) for w in words[: k + 1]]
    value = list(words[k + 2 :])
    # The next listing set against this one: 'R7 - 500k Rev. Log. pot. C7 -
    # 0.001 µF' (Tonepad MAS Distortion).
    nxt = next(
        (
            j
            for j in range(1, len(value) - 2)
            if is_designator(value[j]["text"]) and value[j + 1]["text"] in _DASHES
        ),
        None,
    )
    rest: list[Cell] = []
    if nxt is not None:
        rest = _dash_listing(Cell(tuple(value[nxt:]))) or [Cell(tuple(value[nxt:]))]
        value = value[:nxt]
    # A drawing's pad letters set against the value: '100µF / 16v A B E'.
    letters = 0
    while letters < len(value) and _PAD_LETTER.fullmatch(value[-1 - letters]["text"]):
        letters += 1
    if 2 <= letters < len(value):
        value = value[:-letters]
    return [Cell(tuple(refs)), Cell(tuple(value)), *rest]


_PAD_LETTER = re.compile(r"[A-Z]")


def _listed_ref(text: str) -> bool:
    """A designator or numbered label ('S1') that starts a dashed listing."""
    return is_designator(text) or NUMBERED_LABEL.fullmatch(text) is not None


def _split_merged(cell: Cell) -> list[Cell]:
    """Splits a 'NAME value' cell that a narrow gap failed to separate.

    In tab-aligned columns a long control name can end within an ordinary
    word space of its value ('BLEND B10K'), so both land in one cell. Such a
    cell is split when its first word is a designator or control name and the
    rest is a short value; prose ('D8 and D9 are only needed...') is left whole.
    """
    if REF_RANGE.fullmatch(cell.text) or ref_group(cell.text) is not None:
        return [cell]  # 'Q1 -Q4' is one range (Dr Phybes); 'D1 D2' one list
    listed = _dash_listing(cell)
    if listed is not None:
        return listed
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
        # Not a count: '1 - 25k lin.' (Tonepad).
        and not words[0].isdigit()
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
            if LABEL.fullmatch(" ".join(words[1 : k + 1]))
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
        has_digit(words[start])
        or CONTROL_VALUE.match(rest) is not None
        # 'C16 Not Populated' in a modification list.
        or rest.upper() in constants.NOT_POPULATED_VALUES
    )
    if not value_like:
        if dashed and is_designator(name) and has_digit(name) and rest.isalpha():
            # 'TR1 - BIAS' | '100k' (GuitarPCB PURP): the control's name labels
            # the board designator, and the value is the next cell.
            return [Cell(cell.words[:1])]
        return [cell]
    split = [Cell(cell.words[:1]), Cell(cell.words[start:])]
    if is_designator(name) and name not in constants.BARE_DESIGNATORS:
        return split
    if name in constants.KEYWORDS or (
        CONTROL_NAME.fullmatch(name) and (dashed or CONTROL_VALUE.match(rest))
    ):
        return split
    return [cell]


# Dashed listings that make a page's parts list dashed, as a count and a share.
_DASHED_MIN = 4
_DASHED_SHARE = 0.5
# How far below its value's line a continuation may start, in ems.
_WRAP_GAP_EM = 1.6
# How far right of its designator a wrapped value may start, in ems.
_WRAP_REACH_EM = 8


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
    listings = _undashed_dropped(
        _scan(words, columns, _type_columns(lines)), counted_rows(lines)
    )
    return _mark_trimmers(_wrapped_values(listings, lines), lines)


def _dash_after(line: list[PDFWord], ref: Cell) -> bool:
    """Whether the word after a designator cell on its line is a dash."""
    if any(
        w["x0"] == ref.words[-1]["x0"] and _GLUED_DASH.fullmatch(w["text"])
        for w in line
    ):
        return True  # 'Q1- 2N3904' (Tonepad Filter/Sample/Hold)
    end = ref.words[-1]["x1"]
    after = [
        w for w in line if w["x0"] >= end - 0.5 and w["text"] != ref.words[-1]["text"]
    ]
    if not after:
        return False
    nxt = min(after, key=lambda w: w["x0"])["text"]
    # 'C3 -10n' (GuitarPCB Dr-Phybes): the dash set against the value.
    return nxt in _DASHES or (nxt[:1] in _DASHES and nxt[1:2].isdigit())


def _undashed_dropped(listings: list[Listing], counted: int) -> list[Listing]:
    """Keeps only dashed listings on a page whose parts list writes 'R1 - 10k'.

    Tonepad sets each listing with a dash, or counts parts by value ('2 - 1k'),
    and draws the schematic on the same page; its labels ('AC  ADAPTOR',
    'IC1a  1', 'LED  4k7') read as listings without one.
    """
    dashed = sum(listing.dashed for listing in listings) + counted
    if dashed < _DASHED_MIN or dashed < _DASHED_SHARE * (len(listings) + counted):
        return listings
    return [listing for listing in listings if listing.dashed or not listing.value]


def _wrapped_values(listings: list[Listing], lines: list[list[Cell]]) -> list[Listing]:
    """Gives a designator printed without a value the value wrapped around it.

    A two-line cell centred on its row sets 'CA Bi-colour' on the line above
    the designator and 'LED' on the line below (GuitarPCB Aeon Overdrive v2),
    each alone on its line just right of the designator.
    """
    out = []
    for listing in listings:
        if listing.value:
            out.append(listing)
            continue
        found = []
        for nb in (listing.line - 1, listing.line + 1):
            if not 0 <= nb < len(lines) or len(lines[nb]) != 1:
                continue
            [cell] = lines[nb]
            if (
                listing.x0 < cell.x0 <= listing.x0 + _WRAP_REACH_EM * cell.size
                and not BOARD_REF.fullmatch(cell.text.split()[0])
            ):
                found.append(cell.text)
        value = clean_value(" ".join(found))
        out.append(replace(listing, value=value) if value else listing)
    return out


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

    def typed(cell: Cell) -> bool:
        following = described[id(cell)][len(cell.text) + 1 :] or None
        return describes(cell.text, following)

    typed_cells = [cell for cell in cells if typed(cell)]
    columns = set()
    for cell in typed_cells:
        near = [
            c for c in cells if abs(c.x0 - cell.x0) <= _COLUMN_TOLERANCE_EM * c.size
        ]
        kinds = sum(1 for c in near if typed(c))
        if kinds >= _MIN_COLUMN_REFS and kinds >= _TYPE_SHARE * len(near):
            columns.add(round(cell.x0))
    return frozenset(columns)


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
        if cell.text.split()[0].upper() in HEADER_WORDS
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
            listing = replace(listing, value=f"{listing.value} TRIM")
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
    above_top = 0.0
    for line_no, line in enumerate(group_lines(words)):
        cells = _join_numbered_names(
            [part for cell in split_cells(line) for part in _split_merged(cell)]
        )
        top = line[0]["top"]
        if above and top - above_top > _WRAP_GAP_EM * line[0]["size"]:
            above = []
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
                or not is_ref_cell(texts[i], nxt)
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
                complete = not LABEL.fullmatch(" ".join(parts))
                if types and (
                    (parts == ["LED"] and _LED_SIZE.match(texts[j]))
                    or (
                        VALUE_SHAPE.fullmatch(parts[0]) is not None
                        and describes(texts[j], following)
                    )
                ):
                    # The value's description: 'D5 | LED | 3MM LED',
                    # 'U2 | TL072 | 8 Pin Dual Op-Amp'.
                    break
                if (
                    is_ref_cell(texts[j], following)
                    or _SPACED_REF.fullmatch(texts[j])
                    # A counted part starts: 'IC1 - PT2399 | 1 - 25k lin.' (Tonepad).
                    or COUNTED_ROW.match(texts[j])
                    # A drawing's pad letters: 'C7 - 100µF / 16v | A | B | E'.
                    or (
                        _PAD_LETTER.fullmatch(texts[j]) is not None
                        and following is not None
                        and _PAD_LETTER.fullmatch(following) is not None
                    )
                    or not is_annotation(texts[j])
                    or (complete and _in_column(cells[j].x0, cells[j].size, columns))
                ):
                    break
                parts.append(texts[j])
                j += 1
            if (
                len(parts) > 1
                and is_designator(texts[i])
                and names_control(parts[0])
                and VALUE_SHAPE.match(parts[1])
            ):
                # 'P1 | VOL 1 | 100k Log' (GuitarPCB XX Double Shot): the
                # control's name labels the board designator.
                parts = parts[1:]
            value = clean_value(" ".join(parts))
            if value:
                current.append((cells[i + 1].x0, cells[i + 1].size, len(listings)))
                if _dash_after(line, cells[i]):
                    # A dashed cell wraps under its start: 'IC3 - 5v positive
                    # Regulator' / '(78L05)' (Tonepad Rebote 2.5).
                    current.append((cells[i].x0, cells[i].size, len(listings)))
                listings.append(
                    Listing(
                        ref=_ref_name(texts[i]),
                        value=value,
                        line=line_no,
                        x0=cells[i].x0,
                        dashed=_dash_after(line, cells[i]),
                    )
                )
            following = _text_at(texts, j + 1)
            if (
                types
                and j < len(texts)
                # 'D1 | 5v1 Zener' after a value is the next listing (GuitarPCB
                # Dr-Phybes).
                and not is_ref_cell(texts[j], following)
                and describes(texts[j], following)
            ):
                # Its description names no part: '5K | 6mm Trim Pot'.
                j = _description_end(cells, j, designators)
            i = j
        # A line of another column between the rows (Tonepad's notes beside
        # its parts list) does not part a value from its continuation.
        if current:
            above, above_top = current, top
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
        or NUMBERED_LABEL.fullmatch(cells[k].text)
        or (
            cells[k].text.isupper()
            and is_ref_cell(cells[k].text, _text_at([c.text for c in cells], k + 1))
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
                if abs(cell.x0 - x0) <= LINE_TOLERANCE_EM * size
            ),
            None,
        )
        if target is not None and _CONTINUATION.fullmatch(cell.text):
            prev = listings[target]
            listings[target] = replace(
                prev, value=clean_value(f"{prev.value} {cell.text}")
            )
        else:
            remaining.append(cell)
    return remaining
