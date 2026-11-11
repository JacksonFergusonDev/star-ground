"""Reads typed bills of materials laid out in unruled columns.

'Part | Value | Type | Notes' tables, including those with one value
column per build ('MXR Value | DOD Value').
"""

import math
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum

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
from star_ground.bom_lib.pdf.geometry import LINE_TOLERANCE_EM, Cell
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


class _Role(StrEnum):
    """What a column of a typed table holds."""

    REFS = "refs"
    VALUE = "value"
    QTY = "qty"
    NOTES = "notes"
    TYPE = "type"
    # A later build's values, in a table with one VALUE column per build.
    BUILD = "build"


_BOM_COLUMNS = {
    "VALUE": _Role.VALUE,
    "QUANTITY": _Role.QTY,
    "QTY": _Role.QTY,
    "QT": _Role.QTY,  # 'Qt' / 'y' wrapped in a narrow column (Q-Tune)
    "NOTES": _Role.NOTES,
    "COMMENTS": _Role.NOTES,
    "TYPE": _Role.TYPE,
    "FOOTPRINT": _Role.TYPE,
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
class _Field:
    """Which column of a table section a cell is read into.

    Attributes:
        role: What the column holds.
        build: The build whose values a `_Role.BUILD` column holds; empty
            for the other roles.
    """

    role: _Role
    build: str = ""


_REFS = _Field(_Role.REFS)
_VALUE = _Field(_Role.VALUE)
_QTY = _Field(_Role.QTY)
_NOTES = _Field(_Role.NOTES)
_TYPE = _Field(_Role.TYPE)


@dataclass(frozen=True, slots=True)
class _Column:
    """One column header of a typed table.

    Attributes:
        role: What the column holds.
        x0: Left edge of the header, in points.
        x1: Right edge of the header, in points.
        build: The build a 'VALUE (name)' column holds, including the first,
            which is the table's value column; empty for other columns.
    """

    role: _Role
    x0: float
    x1: float
    build: str = ""

    @property
    def field(self) -> _Field:
        """The field the column's cells are read into."""
        return _Field(self.role, self.build if self.role is _Role.BUILD else "")

    @property
    def center(self) -> float:
        """The header's horizontal middle, in points."""
        return (self.x0 + self.x1) / 2


def _typed(section: list[_Column]) -> bool:
    """Whether a table section has a TYPE column."""
    return any(col.role is _Role.TYPE for col in section)


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
    types = {name for name, role in _BOM_COLUMNS.items() if role is _Role.TYPE}
    return bool(names & _REF_HEADERS) and "VALUE" in names and bool(names & types)


def _header_sections(cells: list[Cell]) -> list[list[_Column]]:
    """The 'Reference | Value | Quantity' header groups on one line, left to right.

    A line can carry several tables side by side (Q-Tune: 'Value Name Qty
    Value Name Qty'); a repeated field starts the next group. 'VALUE (D+) |
    VALUE (250)' are one table's build columns (Aion FX Aphelion): the first
    is the table's value column, the others are `_Role.BUILD` columns.
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
            first = not any(col.role is _Role.VALUE for col in current)
            role = _Role.VALUE if first else _Role.BUILD
        elif name in _REF_HEADERS or (name == _NAME_HEADER and has_value):
            role = _Role.REFS
        elif name in _BOM_COLUMNS:
            role = _BOM_COLUMNS[name]
        else:
            continue
        column = _Column(role, cell.x0, cell.words[-1]["x1"], label)
        if any(col.field == column.field for col in current):
            sections.append(current)
            current = []
        current.append(column)
    sections.append(current)
    return [
        sec for sec in sections if {_Role.REFS, _Role.VALUE} <= {c.role for c in sec}
    ]


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


def _column_for(cell: Cell, sections: list[list[_Column]]) -> tuple[int, _Field]:
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
        if field == _REFS
        and not any(kf == (k, _VALUE) for kf, _ in placed)
        and not _typed(sections[k])
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
            if any(kf == (k, _REFS) for kf, _ in near):
                continue
            found += [c for kf, c in near if kf == (k, _VALUE)]
        for c in found:
            borrowed.add(id(c))
        added += found
    return sorted(cells + added, key=lambda c: c.x0) if added else cells


@dataclass(frozen=True, slots=True)
class BomReading:
    """The typed text tables on a page ('Reference | Value | Quantity | Notes').

    Attributes:
        rows: The rows under every such header on the page, top to bottom.
            Rows whose designator cell is not a list of designators (titles,
            footers, hardware) are skipped.
        note_cells: The NOTES cells of typed tables. Designators mentioned in
            a note ('Mistakenly labeled C19 on initial release', Aion FX
            Fusion) are not listings, so the self-check that looks for
            unextracted designators must not read them.
        valueless: Designators an untyped table lists with an empty value
            cell, ranges expanded. GuitarPCB The Burner prints 'D2' with
            nothing beside it; without a value it is no row, and the part
            would vanish silently.
    """

    rows: list[BomRow]
    note_cells: list[Cell]
    valueless: list[str]


@dataclass
class _Tables:
    """What reading a page's typed tables has found so far, line by line.

    Attributes:
        sections: The table sections the header lines so far put in force.
        tables: How many tables the header lines so far have started.
        table_of: The table each section belongs to, keyed by its first
            column's x0.
        last_row: Per table section, the index in `rows` of its latest row.
        borrowed: Ids of value cells read into a neighbouring line's row
            (`_borrow_offset_values`).
        rows: The rows read.
        note_cells: The NOTES cells of typed tables.
        valueless: Designators listed with an empty value cell.
    """

    sections: list[list[_Column]] = field(default_factory=list)
    tables: int = 0
    table_of: dict[float, int] = field(default_factory=dict)
    last_row: dict[int, int] = field(default_factory=dict)
    borrowed: set[int] = field(default_factory=set)
    rows: list[BomRow] = field(default_factory=list)
    note_cells: list[Cell] = field(default_factory=list)
    valueless: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _LineFields:
    """One line's cells, read into the table sections' fields.

    Attributes:
        texts: Each section's fields' text, keyed (section, field).
        first_x0: Each section's leftmost designator or value cell.
        end: Left edge of a table beside the line's rows, whose listings are
            not part of them; infinite when there is none.
    """

    texts: dict[tuple[int, _Field], str]
    first_x0: dict[int, float]
    end: float

    def text(self, k: int, field: _Field) -> str:
        """A section's field's text on the line; empty when it has none."""
        return self.texts.get((k, field), "")


def read_bom(lines: list[list[Cell]]) -> BomReading:
    """Reads text tables headed 'Reference | Value | Quantity | Notes'.

    Unlike a ruled table, nothing but alignment marks the columns, and one row
    lists several designators (Sheepylove: 'R10-R12,R18  47k  4'). Several
    tables may sit side by side under one header line, and a later header
    line replaces only the tables it overlaps.

    Args:
        lines: The cells of every line on one page (`page_lines`).

    Returns:
        The tables' rows, NOTES cells, and designators without a value.
    """
    tables = _Tables()
    for line_no, cells in enumerate(lines):
        found = _header_sections(cells)
        if found:
            cells = _start_tables(tables, found, lines, line_no, cells)
        if not tables.sections:
            continue
        cells = [c for c in cells if id(c) not in tables.borrowed]
        cells = _borrow_offset_values(
            cells, lines, line_no, tables.sections, tables.borrowed
        )
        line = _line_fields(cells, tables)
        for k, section in enumerate(tables.sections):
            _read_row(tables, k, section, line, line_no)
    return BomReading(tables.rows, tables.note_cells, tables.valueless)


def _start_tables(
    tables: _Tables,
    found: list[list[_Column]],
    lines: list[list[Cell]],
    line_no: int,
    cells: list[Cell],
) -> list[Cell]:
    """Puts a header line's table sections in force over those they overlap.

    Args:
        tables: The reading so far; updated.
        found: The header line's sections (`_header_sections`).
        lines: Every line of the page.
        line_no: The header line's index.
        cells: The header line's cells.

    Returns:
        The rest of the line, a row of the tables beside the new header.
    """
    lo = min(col.x0 for sec in found for col in sec)
    hi = max(col.x1 for sec in found for col in sec)
    kept = [
        sec
        for sec in tables.sections
        if max(c.x1 for c in sec) < lo or min(c.x0 for c in sec) > hi
    ]
    tables.sections = sorted(kept + found, key=lambda sec: sec[0].x0)
    tables.last_row = {}
    # A header line starts one table per board it heads, numbered after
    # every table before it.
    groups = _board_groups(found, lines[line_no - 1] if line_no else [])
    for sec, group in zip(found, groups, strict=True):
        tables.table_of[sec[0].x0] = tables.tables + group
    tables.tables += max(groups) + 1
    return [c for c in cells if c.x0 < lo or c.x0 > hi]


def _line_fields(cells: list[Cell], tables: _Tables) -> _LineFields:
    """Reads a line's cells into the fields of the sections in force.

    The NOTES cells of typed sections are recorded on `tables` as they are read.

    Args:
        cells: The line's cells, after borrowing offset values.
        tables: The reading so far.

    Returns:
        The line's fields.
    """
    sections = tables.sections
    texts: dict[tuple[int, _Field], str] = {}
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
            target[1] == _TYPE
            and target in texts
            # OCR can read a listing as one box: 'FREQ B5K'.
            and is_ref_cell(first, rest or next_text)
        ):
            # A listing after the TYPE cell starts a second table whose
            # header was not read (Five Cats prints two 'Location | Value |
            # Type' tables side by side; OCR keeps only one header).
            end = cell.x0
            break
        texts[target] = f"{texts.get(target, '')} {cell.text}".strip()
        if target[1] in (_REFS, _VALUE):
            # A row's x0 is its designator's; 'R1 - 1M' sits under Value.
            first_x0.setdefault(target[0], cell.x0)
        if target[1] == _NOTES and _typed(sections[target[0]]):
            tables.note_cells.append(cell)
    return _LineFields(texts, first_x0, end)


def _read_row(
    tables: _Tables, k: int, section: list[_Column], line: _LineFields, line_no: int
) -> None:
    """Reads one table section's row on a line, if it holds one.

    A typed row with only a NOTES cell continues the row above's notes; an
    untyped row with designators and no value records them as valueless.

    Args:
        tables: The reading so far; updated.
        k: The section's index among those in force.
        section: The section's columns.
        line: The line's fields.
        line_no: The line's index.
    """
    typed = _typed(section)
    refs, count = _designators(line.text(k, _REFS))
    refs, value_cell = _dashed_listing(refs, line.text(k, _REFS), line.text(k, _VALUE))
    refs = _designator_of(refs)
    value = drop_remarks(_without_unclosed_aside(ASIDE.sub("", value_cell)))
    # 'SW - SPDT | On-Off_On' (GuitarPCB Skyn-Tone): the pole type in the
    # label belongs to the value.
    poles = _POLES_LABEL.fullmatch(refs)
    if poles:
        refs, value = poles.group(1), f"{poles.group(2)} {value}".strip()
    notes = line.text(k, _NOTES)
    last = tables.last_row.get(k)
    if typed and not refs and notes and last is not None:
        # A NOTES cell wrapped onto the next line, under its row.
        row = tables.rows[last]
        tables.rows[last] = replace(row, notes=f"{row.notes} {notes}")
        return
    marking = _MARKING.search(value)
    if marking:
        value = value[: marking.start()]
    value = _without_control_name(value)
    # A typed table's TYPE explains any label; an untyped grid's label
    # ('Part | Value' grids: 'VOL | A100k') is judged by its value later.
    labeled = _is_label(refs) and (not typed or line.text(k, _TYPE))
    if not value and _is_ref_list(refs) and not typed:
        tables.valueless += [r for r in expand_refs(refs) if is_designator(r)]
    if not value or not (_is_ref_list(refs) or labeled):
        return
    # Optional-part markers are not part of the value ('*100p', '330k*').
    value = " ".join(value.replace("*", " ").split()) or value
    qty = line.text(k, _QTY) or count
    tables.last_row[k] = len(tables.rows)
    tables.rows.append(
        BomRow(
            refs=refs,
            value=value,
            quantity=_quantity(qty),
            line=line_no,
            x0=line.first_x0[k],
            marking=marking.group(1) if marking else "",
            type_text=line.text(k, _TYPE),
            notes=notes,
            table=tables.table_of[section[0].x0],
            end=line.end,
            builds=_build_values(section, line, k, value),
        )
    )


def _designators(cell: str) -> tuple[str, str]:
    """A designator cell's designators, and the count printed after them.

    GuitarPCB prints '* CLR x1', 'CLR x 2', '*DRIVE', 'ORDER switch'.

    Returns:
        The designators, separators unspaced ('C4,C5'), and the count ('1'
        for 'CLR x1'), empty when none is printed.
    """
    refs = _LIST_SPACING.sub(r"\1", cell)
    refs = " ".join(refs.replace("*", " ").split())
    count = _REF_COUNT.search(refs)
    if count:
        refs = refs[: count.start()]
    return _LABEL_NOUN.sub("", refs), count.group(1) if count else ""


def _dashed_listing(refs: str, refs_cell: str, value_cell: str) -> tuple[str, str]:
    """Splits 'R1 - 1M' centred under a 'Part Value' header (GuitarPCB Blues Buster).

    The designator and its value land in one cell, or run across both.

    Args:
        refs: The designators read so far (`_designators`).
        refs_cell: The designator cell as printed.
        value_cell: The value cell as printed.

    Returns:
        The designators and the value cell.
    """
    dashed = _DASHED_LISTING.fullmatch(f"{refs_cell} {value_cell}".strip())
    if (
        dashed
        and is_designator(dashed.group(1))
        and VALUE_SHAPE.fullmatch(dashed.group(2))
    ):
        return dashed.group(1), dashed.group(2)
    return refs, value_cell


def _designator_of(refs: str) -> str:
    """The board designator a labelled designator cell names.

    'TR1 - BIAS' (GuitarPCB PURP) names the control; 'SW1 Tap Tempo', 'S3
    Tempo Scale' (GuitarPCB D'Lay) give the part's role.
    """
    named = NAMED_DESIGNATOR.fullmatch(refs)
    if named and is_designator(named.group(1)):
        refs = named.group(1)
    role = _DESIGNATOR_ROLE.fullmatch(refs)
    return role.group(1) if role else refs


def _without_control_name(value: str) -> str:
    """A value without the control name printed before it.

    'P1 | VOL 1 | 100k Log' (GuitarPCB XX Double Shot).
    """
    for size in (2, 1):
        head = " ".join(value.split()[:size])
        tail = " ".join(value.split()[size:])
        if tail and names_control(head) and VALUE_SHAPE.match(tail):
            return tail
    return value


def _build_values(
    section: list[_Column], line: _LineFields, k: int, value: str
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
        if col.role is not _Role.VALUE:
            raw = _without_unclosed_aside(ASIDE.sub("", line.text(k, col.field)))
            marking = _MARKING.search(raw)
            if marking:
                raw = raw[: marking.start()]
            cell = " ".join(raw.replace("*", " ").split()) or value
            if cell in _SAME_AS_FIRST:
                cell = value  # '_' (GuitarPCB Emexar v4): as the first build
        out.append((col.build, cell))
    return tuple(out)


# A Quantity cell: digits, perhaps with a footnote mark set after them ('2¹').
_QUANTITY = re.compile(r"([0-9]+)[\u00b9\u00b2\u00b3\u2070\u2074-\u2079]*")


def _quantity(cell: str) -> int | None:
    """How many of a row's part a Quantity cell asks for, if it's a number.

    A superscript after the digits is a footnote mark, not part of the
    number; a superscript alone ('²') is no quantity.
    """
    match = _QUANTITY.fullmatch(cell)
    return int(match.group(1)) if match else None


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
