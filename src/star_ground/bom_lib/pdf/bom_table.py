"""Reads typed bills of materials laid out in unruled columns.

'Part | Value | Type | Notes' tables, including those with one value
column per build ('MXR Value | DOD Value').
"""

import math
import re
from dataclasses import dataclass, replace

from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.pdf.cells import (
    ASIDE,
    NAMED_DESIGNATOR,
    VALUE_SHAPE,
    drop_remarks,
    has_digit,
    is_prose,
    is_ref_cell,
    names_control,
)
from star_ground.bom_lib.pdf.geometry import LINE_TOLERANCE_EM, Cell, page_lines
from star_ground.bom_lib.types import PDFWord
from star_ground.bom_lib.utils import expand_refs

# 'R1 - 1M' (or an en dash): a designator and its value, a dash apart.
_DASHED_LISTING = re.compile(r"(\S+)\s+[-\u2013\u2014]\s+(\S+)")


# Column headers of a text bill of materials ('Reference  Value  Quantity  Notes',
# Aion FX's 'PART  VALUE  TYPE  NOTES').
_REF_HEADERS = frozenset(
    {"REFERENCE", "REFERENCES", "REF", "REFS", "DESIGNATOR", "PART", "LOCATION"}
)
# 'Name' heads the designator column only beside a 'Value' column (Q-Tune's
# 'Value | Name | Qty' packing list).
_NAME_HEADER = "NAME"
# One VALUE column per build: 'VALUE (D+) | VALUE (250)' (Aion FX Aphelion).
# 'MXR Value | DOD Value' (GuitarPCB Emexar v4) names the build before the word;
# 'Old Value | New Value' (Aion FX Comet), 'Stock Value | Parallel Value' (Aion FX
# Protolith) describe changes or alternatives, not two builds.
_BUILD_VALUE_HEADER = re.compile(
    r"VALUE \((?P<build>[^()]+)\)|(?P<before>(?!(?:PART|REF|OLD|NEW|STOCK|MOD|PARALLEL|COMBINED|NEAREST|ORIGINAL|ALT|ALTERNATE|SUGGESTED|DEFAULT|KIT)\b)[A-Z0-9+]{2,8}) VALUE",
    re.IGNORECASE,
)
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
# A part's printed marking is not its value: '100n (104)', '1N4148 (41 48)',
# '100n (.100J63)'. Words in parentheses ('(low ESR)', '(KY5050)') stay.
_MARKING = re.compile(r"\s*\(\s*(\.?\d[\d .]*(?:[JKM]\d+)?)\s*\)$")
# Separators inside a printed designator list: 'R11, R13', 'D21 - D28'.
_DESIGNATOR_SHAPE = re.compile(r"[A-Za-z]{1,6}\d*[A-Za-z]{0,3}")
_LIST_SPACING = re.compile(r"\s*([,\-\u2013])\s*")


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
        builds: Each build's name and value when the table has one VALUE column
            per build ('VALUE (D+) | VALUE (250)'); empty otherwise. `value`
            is the first build's.
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
    builds: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _Column:
    field: str
    x0: float
    x1: float
    build: str = ""  # the build a 'VALUE (name)' column holds

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def _without_unclosed_aside(text: str) -> str:
    """Drops a parenthesis left open at the end of a cell, and what follows it.

    A wrapped value loses its closing parenthesis: '1N5226 (1N5' / '226)'.
    The open aside starts at the first '(' after the last ')'. A regex
    searching for a '(' with no ')' after it would rescan the rest of the
    cell from every '(', quadratic in a run of them.
    """
    start = text.find("(", text.rfind(")") + 1)
    return text if start < 0 else text[:start].rstrip()


def heads_typed_table(words: list[PDFWord]) -> bool:
    """Whether a line of words heads a typed table ('Ref | Value | Footprint')."""
    names = {w["text"].upper() for w in words}
    types = {name for name, field in _BOM_COLUMNS.items() if field == "type"}
    return bool(names & _REF_HEADERS) and "VALUE" in names and bool(names & types)


def _header_sections(cells: list[Cell]) -> list[list[_Column]]:
    """The 'Reference | Value | Quantity' header groups on one line, left to right.

    A line can carry several tables side by side (Q-Tune: 'Value Name Qty
    Value Name Qty'); a repeated field starts the next group. 'VALUE (D+) |
    VALUE (250)' are one table's build columns (Aion FX Aphelion): the first
    is the table's value column, the others are fields named 'build:<name>'.
    """
    has_value = any(
        c.text.upper() == "VALUE" or _BUILD_VALUE_HEADER.fullmatch(c.text)
        for c in cells
    )
    sections: list[list[_Column]] = []
    current: list[_Column] = []
    for cell in cells:
        name = cell.text.upper()
        build = _BUILD_VALUE_HEADER.fullmatch(cell.text)
        label = ""
        if build:
            label = (build.group("build") or build.group("before")).strip()
            first = not any(col.field == "value" for col in current)
            field_name = "value" if first else f"build:{label}"
        elif name in _REF_HEADERS or (name == _NAME_HEADER and has_value):
            field_name = "refs"
        elif name in _BOM_COLUMNS:
            field_name = _BOM_COLUMNS[name]
        else:
            continue
        if any(col.field == field_name for col in current):
            sections.append(current)
            current = []
        current.append(_Column(field_name, cell.x0, cell.words[-1]["x1"], label))
    sections.append(current)
    return [sec for sec in sections if {"refs", "value"} <= {col.field for col in sec}]


def _board_groups(sections: list[list[_Column]], above: list[Cell]) -> list[int]:
    """Which board's table each header section belongs to, from the headings above.

    One header line can head two boards' tables side by side, each under its
    own heading ('FrontStage' and 'DumbleTone', GuitarPCB DumbleTone Deluxe):
    a section belongs to the nearest heading. Without such headings, all
    sections are one table.
    """
    headings = [c for c in above if not has_digit(c.text) and len(c.text.split()) <= 3]
    if (
        len(headings) < 2
        or len(headings) != len(above)
        or len(headings) > len(sections)
    ):
        return [0] * len(sections)

    def center(x0: float, x1: float) -> float:
        return (x0 + x1) / 2

    return [
        min(
            range(len(headings)),
            key=lambda h: abs(
                center(sec[0].x0, sec[-1].x1)
                - center(headings[h].x0, headings[h].words[-1]["x1"])
            ),
        )
        for sec in sections
    ]


def _column_for(cell: Cell, sections: list[list[_Column]]) -> tuple[int, str]:
    """The section and field a cell belongs to.

    A cell that starts at a header belongs to it (left-aligned tables); any
    other cell goes to the header nearest its center (Q-Tune centers its cells).
    """
    center = (cell.x0 + cell.words[-1]["x1"]) / 2
    tolerance = LINE_TOLERANCE_EM * cell.size
    columns = [(k, col) for k, sec in enumerate(sections) for col in sec]
    for k, col in columns:
        if abs(cell.x0 - col.x0) <= tolerance:
            return k, col.field
    k, col = min(columns, key=lambda kc: abs(center - kc[1].center))
    return k, col.field


def _borrow_offset_values(
    cells: list[Cell],
    lines: list[list[Cell]],
    line_no: int,
    sections: list[list[_Column]],
    borrowed: set[int],
) -> list[Cell]:
    """Gives a label with an empty value cell the value set half a line off.

    GuitarPCB Tube Screaming Ultra prints its last column's values a little
    above their labels, so 'LEVEL' and 'A100k' land on separate lines and the
    pot would vanish. A value cell on the line just above or below, on a line
    with no label of its own in that table, is the label's value; a value
    ending in 'or' continues on the other side ('TL072 or' / 'IC1' /
    'NE5532', D'Lay).

    Args:
        cells: The current line's cells.
        lines: Every line of the page.
        line_no: The current line's index.
        sections: The table sections in force.
        borrowed: Ids of cells already read into another line; updated.

    Returns:
        The line's cells with the borrowed value cells added.
    """
    placed = [(_column_for(c, sections), c) for c in cells]
    empty = [
        k
        for (k, field), _ in placed
        if field == "refs"
        and not any(kf == (k, "value") for kf, _ in placed)
        and not any(col.field == "type" for col in sections[k])
    ]
    added: list[Cell] = []
    for k in empty:
        found: list[Cell] = []
        for nb in (line_no - 1, line_no + 1):
            if not 0 <= nb < len(lines) or _header_sections(lines[nb]):
                continue
            if found and not found[-1].text.endswith(" or"):
                break
            near = [
                (_column_for(c, sections), c)
                for c in lines[nb]
                if id(c) not in borrowed
            ]
            if any(kf == (k, "refs") for kf, _ in near):
                continue
            found += [c for kf, c in near if kf == (k, "value")]
        for c in found:
            borrowed.add(id(c))
        added += found
    return sorted(cells + added, key=lambda c: c.x0) if added else cells


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


def bom_valueless_refs(words: list[PDFWord]) -> list[str]:
    """Designators a text table lists with an empty value cell.

    GuitarPCB The Burner prints 'D2' with nothing beside it; without a value it
    is no row, and the part would vanish silently.

    Args:
        words: Words extracted from one page.

    Returns:
        The designators, ranges expanded.
    """
    return _read_bom(words)[2]


def _read_bom(words: list[PDFWord]) -> tuple[list[BomRow], list[Cell], list[str]]:
    rows: list[BomRow] = []
    note_cells: list[Cell] = []
    valueless: list[str] = []
    sections: list[list[_Column]] = []
    # Per table section, the index in `rows` of its latest row.
    last_row: dict[int, int] = {}
    # The table each section belongs to, keyed by its first column's x0.
    table_of: dict[float, int] = {}
    header_no = -1
    lines = page_lines(words)
    # Value cells read into a neighbouring line's row (`_borrow_offset_values`).
    borrowed: set[int] = set()
    for line_no, cells in enumerate(lines):
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
            for sec, group in zip(
                found,
                _board_groups(found, lines[line_no - 1] if line_no else []),
                strict=True,
            ):
                table_of[sec[0].x0] = header_no + group
            # The rest of the line is a row of the tables beside the new header.
            cells = [c for c in cells if c.x0 < lo or c.x0 > hi]
        if not sections:
            continue
        cells = [c for c in cells if id(c) not in borrowed]
        cells = _borrow_offset_values(cells, lines, line_no, sections, borrowed)
        fields: dict[tuple[int, str], str] = {}
        first_x0: dict[int, float] = {}
        end = math.inf
        right = max(col.x1 for sec in sections for col in sec)
        for n, cell in enumerate(cells):
            if cell.x0 > right + _BEYOND_TABLE_EM * cell.size and is_prose(cell.text):
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
                and is_ref_cell(first, rest or next_text)
            ):
                # A listing after the TYPE cell starts a second table whose
                # header was not read (Five Cats prints two 'Location | Value |
                # Type' tables side by side; OCR keeps only one header).
                end = cell.x0
                break
            fields[target] = f"{fields.get(target, '')} {cell.text}".strip()
            if target[1] in ("refs", "value"):
                # A row's x0 is its designator's; 'R1 - 1M' sits under Value.
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
            whole = f"{fields.get((k, 'refs'), '')} {fields.get((k, 'value'), '')}"
            dashed = _DASHED_LISTING.fullmatch(whole.strip())
            if (
                dashed
                and is_designator(dashed.group(1))
                and VALUE_SHAPE.fullmatch(dashed.group(2))
            ):
                # 'R1 - 1M' centred under a 'Part Value' header (GuitarPCB Blues
                # Buster): the designator and its value in one cell.
                refs = dashed.group(1)
                fields[(k, "value")] = dashed.group(2)
            # 'TR1 - BIAS' (GuitarPCB PURP): the control's name labels the
            # board designator.
            named = NAMED_DESIGNATOR.fullmatch(refs)
            if named and is_designator(named.group(1)):
                refs = named.group(1)
            # 'SW1 Tap Tempo', 'S3 Tempo Scale' (GuitarPCB D'Lay): the part's
            # role after its designator.
            role = _DESIGNATOR_ROLE.fullmatch(refs)
            if role:
                refs = role.group(1)
            value = drop_remarks(
                _without_unclosed_aside(ASIDE.sub("", fields.get((k, "value"), "")))
            )
            # 'SW - SPDT | On-Off_On' (GuitarPCB Skyn-Tone): the pole type in
            # the label belongs to the value.
            poles = _POLES_LABEL.fullmatch(refs)
            if poles:
                refs, value = poles.group(1), f"{poles.group(2)} {value}".strip()
            notes = fields.get((k, "notes"), "")
            last = last_row.get(k)
            if typed and not refs and notes and last is not None:
                # A NOTES cell wrapped onto the next line, under its row.
                rows[last] = replace(rows[last], notes=f"{rows[last].notes} {notes}")
                continue
            marking = _MARKING.search(value)
            if marking:
                value = value[: marking.start()]
            for size in (2, 1):
                head = " ".join(value.split()[:size])
                tail = " ".join(value.split()[size:])
                if tail and names_control(head) and VALUE_SHAPE.match(tail):
                    # 'P1 | VOL 1 | 100k Log' (GuitarPCB XX Double Shot).
                    value = tail
                    break
            # A typed table's TYPE explains any label; an untyped grid's label
            # ('Part | Value' grids: 'VOL | A100k') is judged by its value later.
            labeled = _is_label(refs) and (not typed or fields.get((k, "type")))
            if not value and _is_ref_list(refs) and not typed:
                valueless += [r for r in expand_refs(refs) if is_designator(r)]
            if not value or not (_is_ref_list(refs) or labeled):
                continue
            # Optional-part markers are not part of the value ('*100p', '330k*').
            value = " ".join(value.replace("*", " ").split()) or value
            builds = _build_values(sections[k], fields, k, value)
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
                    table=table_of.get(sections[k][0].x0, header_no),
                    end=end,
                    builds=builds,
                )
            )
    return rows, note_cells, valueless


def _build_values(
    section: list[_Column], fields: dict[tuple[int, str], str], k: int, value: str
) -> tuple[tuple[str, str], ...]:
    """Each build's value in a table with one VALUE column per build.

    The first build's value is the row's (already cleaned) value. A blank
    build cell shares it: Aion FX Elite prints its hardware rows' single value
    under the first build only.
    """
    named = [col for col in section if col.build]
    if len(named) < 2:
        return ()
    out = []
    for col in named:
        cell = value
        if col.field != "value":
            raw = _without_unclosed_aside(ASIDE.sub("", fields.get((k, col.field), "")))
            marking = _MARKING.search(raw)
            if marking:
                raw = raw[: marking.start()]
            cell = " ".join(raw.replace("*", " ").split()) or value
            if cell in _SAME_AS_FIRST:
                cell = value  # '_' (GuitarPCB Emexar v4): as the first build
        out.append((col.build, cell))
    return tuple(out)


# 'CLR x1', 'CLR x 2': how many of the part to buy.
# A build cell that repeats the first build's value.
_SAME_AS_FIRST = frozenset({"_", "-", "\u2013", '"', "\u201d"})
# 'CLR - 1' (GuitarPCB Cock-It-Wah) reads 'CLR-1' once its spacing is closed up.
_REF_COUNT = re.compile(r"(?:\s+x\s*|(?<=\bCLR)-)(\d+)$")
# 'ORDER switch', 'DEEP switch': the noun restates the category.
_LABEL_NOUN = re.compile(r"(?<=[A-Za-z0-9]) (?:switch|pot|trimmer)$")
# 'TR1-BIAS' names TR1's control; 'IC1-SOCKET' (Aion Amethyst) is a part of its own.
_POLES_LABEL = re.compile(r"([A-Z]{1,4}\d{0,3}) ?- ?([SD34]P[SDT]T)")


# 'S2 PWM Speed Cycle': a role may start with an acronym. A capitalized word alone
# ('Q5 BIAS', 'LFO1 RATE', Aion) is the control's name, not a role.
_DESIGNATOR_ROLE = re.compile(
    r"([A-Z]{1,3}\d{1,3}) (?:[A-Z][a-z]+|[A-Z]{2,4}(?= [A-Z][a-z]))(?: [A-Z][a-z]+)*"
)
# A part label in a typed table: 'VOL.', 'HI-MID FREQ', 'IC1-S', 'PRE-V. SEND',
# '100HZ' (Aion FX Karkadann's 100 Hz EQ pot).
_PART_LABEL = re.compile(
    r"(?:[A-Z]|\d+[A-Z]{2,}\b)[A-Za-z0-9_./+-]*(?: [A-Za-z0-9_./+-]+){0,2}"
)


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
