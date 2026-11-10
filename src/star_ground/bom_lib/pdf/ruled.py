"""Reads the ruled tables pdfplumber finds on a page.

Ruled tables are the most reliable source: each cell is already a
designator, a value or a note. This module also re-pairs shaded grids
and reads tables whose columns are the builds of a multi-circuit document.
"""

import re
from dataclasses import dataclass, replace

from star_ground.bom_lib.builds import Entry
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.enrichment import capacitor_notes, drop_restated_value
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.pdf.cells import (
    REF_RANGE,
    control_designator,
    drop_remarks,
    names_control,
    strip_asides,
)
from star_ground.bom_lib.pdf.page import PDFPageExtraction
from star_ground.bom_lib.pdf.rows import (
    CONTROL_LABEL,
    RATING_CELL,
    ZERO_WIDTH,
    is_ignored,
    is_not_populated,
)
from star_ground.bom_lib.pdf.text_tables import typed_row
from star_ground.bom_lib.types import (
    ReadingStats,
)
from star_ground.bom_lib.utils import expand_refs, parse_value_to_decimal
from star_ground.bom_lib.verification import (
    check_alternate_value,
    check_version_note,
)

_REF_QUANTITY = re.compile(r"\s*\(\s*qty\.?\s*(\d+)\s*\)\s*$", re.IGNORECASE)


# Longer than any value: a headerless 'table' cell holding prose.
_PROSE_CHARS = 60


# A designator cell holds at most this many words ('SW1 - SW2', 'IC1 & IC2').
_REF_WORDS_MAX = 4


_CHOOSE_ROW = re.compile(r"\s*choose between .+ or ", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _TableTitle:
    """The lone title cell above a table ('Dist +™'), and the page it is on."""

    page: int
    name: str


def table_entries(
    pages_data: list[PDFPageExtraction],
    stats: ReadingStats,
    only_pages: set[int] | None = None,
) -> list[Entry]:
    """Strategy 1: Extract entries from ruled tables (LOCATION / VALUE / TYPE / NOTES).

    Args:
        pages_data: Pre-extracted page data.
        stats: The reading's counts and warnings, updated in place.
        only_pages: If given, the only pages to read.

    Returns:
        One entry per table row that names a designator and a value.
    """
    entries: list[Entry] = []
    seen_refs: set[str] = set()
    table_no = -1
    titles: dict[int, _TableTitle] = {}
    shaded_seen: set[tuple[int, str, str]] = set()
    for page_no, data in enumerate(pages_data):
        if only_pages is not None and page_no not in only_pages:
            continue
        heading = _SLASH_VERSIONS.search(data.text)
        versions = (heading.group(1), heading.group(2)) if heading else None
        for table in data.tables:
            if not table:
                continue
            table_no += 1

            this_table: list[Entry] = []
            if _alternate_spec_table(table, entries, stats):
                continue
            grid = _value_column_grid(table, page_no, table_no)
            if grid is not None:
                entries += grid
                continue

            # 'Dist +™' over 'Part | Value' (GuitarPCB EMEXAR): the table's title.
            title = _table_title(table)
            if title is not None:
                titles[table_no] = _TableTitle(page_no, title)
                table = table[1:]
            # Header mapping. A blank header cell keeps its place, so each
            # header's index is its column's: '| Part | Value' has a blank
            # first column.
            headers = [str(h).upper().strip() if h else "" for h in table[0]]
            loc_idx = -1
            val_idx = -1
            type_idx = -1
            notes_idx = -1
            start_row_idx = 1

            for i, h in enumerate(headers):
                if h in ("LOCATION", "REF", "DESIGNATOR", "PART"):
                    loc_idx = i
                elif h in ("VALUE", "VAL", "DESCRIPTION"):
                    val_idx = i
                elif h == "TYPE":
                    type_idx = i
                elif h in ("NOTES", "COMMENTS"):
                    # GuitarPCB Fuzzy Bee: 'Part | Value | Comments'.
                    notes_idx = i

            if loc_idx == -1 and {"QTY", "QUANTITY"} & set(headers):
                # ProtoBoard: 'QTY | PART NUMBER | DESCRIPTION' lists no designators.
                continue

            # Fallback mapping
            headerless = loc_idx == -1 or val_idx == -1
            if headerless:
                loc_idx, val_idx, start_row_idx = 0, 1, 0
                # The table is also read pair by pair; those rows only fill in
                # for the other readers (`fill_in`).
                for ref, value in _shaded_pairs(table) or []:
                    value = drop_remarks(strip_asides(" ".join(value.split())))
                    if (
                        ref
                        and value
                        and not is_ignored(value)  # 'BIAS | TP' is a test point
                        # 'C8A | * | 63V' (MoWah) gives only a rating: the value
                        # is in the notes.
                        and not RATING_CELL.fullmatch(value.strip("* "))
                        and (page_no, ref, value) not in shaded_seen
                    ):
                        shaded_seen.add((page_no, ref, value))
                        entries.append(
                            Entry(ref, value, page_no, table=table_no, shaded=True)
                        )

            follows_or = False
            for row in table[start_row_idx:]:
                stats.lines_read += 1
                row_safe = [str(cell) if cell else "" for cell in row]
                filled = [c.strip() for c in row_safe if c.strip()]
                if filled and (
                    all(c.upper() == "OR" for c in filled)
                    # Spelunker: 'Choose between internal trimpot or external
                    # potentiometer, do not install both'.
                    or (len(filled) == 1 and _CHOOSE_ROW.match(filled[0]))
                ):
                    # Shindig: an 'OR' row between two alternatives for one spot.
                    follows_or = True
                    continue

                # Skip Summary lines (e.g., "1 x 100k")
                first_content = next((c for c in row_safe if c), "")
                if re.match(r"^\d+\s*[xX]", first_content):
                    continue

                ref_raw = ""
                val_raw = ""
                hint: ComponentCategory | None = None

                if len(row_safe) > max(loc_idx, val_idx):
                    # A footnote marker is no part of the designator ('*BOOST').
                    ref_raw = row_safe[loc_idx].replace("\n", " ").strip()
                    ref_raw = ref_raw.lstrip("*").strip() or ref_raw
                    # 'Order switch' (GuitarPCB Wylde Child): the noun restates
                    # the category, as in text tables.
                    ref_raw = _RULED_LABEL_NOUN.sub("", ref_raw)
                    # 'IC1 / & / IC2' (GuitarPCB Disturbance): two designators.
                    pair = _REF_PAIR.fullmatch(ref_raw)
                    if pair and all(is_designator(g) for g in pair.groups()):
                        ref_raw = f"{pair.group(1)},{pair.group(2)}"
                    named = _NAMED_REF.match(ref_raw)
                    if named and "=" in ref_raw:
                        ref_raw = named.group(1)  # 'P1=FUZZ' (GuitarPCB PUFF v4)
                    # 'P2-Volume' (GuitarPCB Sho' Nuff).
                    ref_raw = control_designator(ref_raw) or ref_raw
                    # 'RX2 (by IC1)', 'RX2 (by switch)' (Aion FX legacy Aurora):
                    # two positions printed with one designator.
                    position = _POSITION_ASIDE.fullmatch(ref_raw)
                    if position and is_designator(position.group(1)):
                        ref_raw = position.group(1)
                        if any(e.ref == ref_raw for e in this_table):
                            ref_raw = f"{ref_raw} (2)"
                    # 'TR1 (see text)', 'TR2 (Volume)' (GuitarPCB D'Lay): an aside
                    # on the designator.
                    aside = _REF_ASIDE.fullmatch(ref_raw)
                    note = (
                        " ".join(ZERO_WIDTH.sub("", aside.group(2)).split())
                        if aside
                        else ""
                    )
                    if (
                        aside
                        and is_designator(aside.group(1))
                        and (note.lower().startswith("see ") or names_control(note))
                    ):
                        ref_raw = aside.group(1)
                    val_raw = " ".join(row_safe[val_idx].split())
                    # Optional-part markers are not part of the value ('*27K*'),
                    # but a value of only markers is a placeholder and stays one.
                    unmarked = " ".join(val_raw.replace("*", " ").split())
                    if unmarked:
                        val_raw = unmarked
                    if 0 <= type_idx < len(row_safe):
                        notes = (
                            row_safe[notes_idx]
                            if 0 <= notes_idx < len(row_safe)
                            else ""
                        )
                        val_raw, hint = typed_row(
                            stats, ref_raw, val_raw, row_safe[type_idx], notes
                        )
                    # '100k (see text)' (GuitarPCB Mini-Me): the aside is no value.
                    val_raw = drop_restated_value(strip_asides(val_raw))
                    if 0 <= notes_idx < len(row_safe):
                        val_raw = capacitor_notes(ref_raw, val_raw, row_safe[notes_idx])
                        if not is_not_populated(val_raw):
                            check_version_note(stats, ref_raw, row_safe[notes_idx])
                elif len(row_safe) == 1 and row_safe[0]:
                    parts = row_safe[0].strip().split(None, 1)
                    if len(parts) == 2:
                        ref_raw, val_raw = parts[0].strip(), parts[1].strip()

                if headerless and len(val_raw) > _PROSE_CHARS:
                    # A page border drawn as a box reads as a one-cell 'table'
                    # holding the page's prose (GuitarPCB): not a parts row.
                    continue
                if headerless and len(ref_raw.split()) > _REF_WORDS_MAX:
                    # A box around board artwork reads as a 'table' of its
                    # labels: '50k 50k trim p2 trim p1 int-ext ...' (Tonepad).
                    continue

                # Auditorium: 'TERMINAL BLOCK (Qty 3)'.
                qty = 1
                quantity = _REF_QUANTITY.search(ref_raw)
                if quantity:
                    qty = int(quantity.group(1))
                    ref_raw = ref_raw[: quantity.start()].strip()

                if versions and " / " not in val_raw:
                    # 'Parts (Ross / Dyna values)' over 'R2 | 470k | jumper' (Aion
                    # FX legacy Aurora): the second version's value in a column
                    # of its own.
                    second = next(
                        (c.strip() for c in row_safe[val_idx + 1 :] if c.strip()), ""
                    )
                    if _SECOND_VALUE.fullmatch(second):
                        val_raw = f"{val_raw} / {second}"

                if ref_raw and val_raw:
                    for ref, value, version in _version_split(
                        ref_raw, val_raw, versions
                    ):
                        this_table.append(
                            Entry(
                                ref,
                                value,
                                page_no,
                                table=table_no,
                                hint=hint,
                                follows_or=follows_or,
                                qty=qty,
                                column=version,
                            )
                        )
                    follows_or = False
            if _is_change_list(this_table, seen_refs, data.text):
                continue
            seen_refs.update(e.ref for e in this_table)
            entries += this_table
    return _titled_builds(entries, titles)


_VARIANT_SHARE = 0.8
_CONTINUED_ROWS = 3
_TITLE_HEADERS = frozenset({"PART", "VALUE", "LOCATION", "REF"})
_MAX_TITLE_WORDS_CELL = 4
# Side-by-side titled tables are builds when there are this many, listing the
# same designators: two boards side by side (GuitarPCB Tweed Fuzztone) are as
# common as two builds, three identical lists are not.
_MIN_TITLED_BUILDS = 3


def continues_list(data: PDFPageExtraction) -> bool:
    """Whether a page opens with ruled tables of 'designator | value' rows."""
    rows = [
        [str(c).strip() for c in row if c and str(c).strip()]
        for table in data.tables
        for row in table
    ]
    rows = [r for r in rows if r]
    listed = [
        r for r in rows if len(r) == 2 and (is_designator(r[0]) or names_control(r[0]))
    ]
    return len(listed) >= _CONTINUED_ROWS and len(listed) * 2 >= len(rows)


def fill_in(entries: list[Entry], shaded: list[Entry]) -> list[Entry]:
    """Adds the re-read shaded-grid rows the other readers missed or ran on.

    The text and layout readers read most shaded grids well, and their
    positions place each part in its build (GuitarPCB Dr Phybes); the re-read
    rows only add designators they lost (SWAH V4's controls) and replace a value
    that ran into the next cell ('LM13700 P2-Sens B100k Lin').
    """
    if not shaded:
        return entries
    read = {(e.page, e.ref.upper()) for e in entries}
    shaded_values = {(e.page, e.ref.upper()): e.value for e in shaded}
    kept = [
        e
        for e in entries
        if (key := (e.page, e.ref.upper())) not in shaded_values
        or e.column is not None  # per-build columns already split 'MXR | DOD' values
        or not (_runs_on(e.value) or _cut_short(e.value, shaded_values[key]))
    ]
    kept_keys = {(e.page, e.ref.upper()) for e in kept}
    return kept + [
        replace(e, shaded=False)
        for e in shaded
        if (e.page, e.ref.upper()) not in kept_keys
        or (e.page, e.ref.upper()) not in read
    ]


def _cut_short(value: str, reread: str) -> bool:
    """Whether a value lost the second value its cell holds ('10k' of '10k 470k').

    The older Tube Screaming Ultra marks its alternate values only by a fill
    colour; keeping both lets the two-values check warn.
    """
    extra = reread.split()[len(value.split()) :]
    return (
        reread.startswith(value)
        and len(extra) == 1
        and parse_value_to_decimal(extra[0]) is not None
    )


def _runs_on(value: str) -> bool:
    """Whether a value ran into the next cell: another label follows it."""
    if names_control(value):
        return True  # 'P1 | GAIN' read the control's name as its value
    words = value.split()
    return any(
        not _JFET_PART.fullmatch(w)
        and w.upper() != "LED"  # 'CA Bi-colour LED' names the part
        and words[k - 1].lower() not in ("or", "/")  # 'J309 or J113' names alternatives
        and (
            is_designator(w)
            or _NAMED_REF.match(w)
            or (w.isupper() and names_control(w))
        )
        for k, w in enumerate(words)
        if k
    )


def _table_title(table: list[list[str | None]]) -> str | None:
    """A lone title cell above a table's 'Part | Value' header row."""
    if len(table) < 2:
        return None
    filled = [str(c).strip() for c in table[0] if c and str(c).strip()]
    header = {str(c).upper().strip() for c in table[1] if c}
    if (
        len(filled) == 1
        and len(filled[0].split()) <= _MAX_TITLE_WORDS_CELL
        and header & _TITLE_HEADERS
    ):
        return filled[0]
    return None


def _titled_builds(entries: list[Entry], titles: dict[int, _TableTitle]) -> list[Entry]:
    """Names each build of side-by-side titled tables that list the same parts.

    GuitarPCB EMEXAR 2 prints 'Dist +™', 'MicroAmp™' and 'DOD 250™' tables side
    by side, one per project for its one board; each table becomes a value
    column (`builds._column_variants`). The untitled tables that continue them
    on the next page ('GAIN | VOL'), as many and in the same order, follow.
    """
    by_table: dict[int, list[Entry]] = {}
    for e in entries:
        if e.table is not None:
            by_table.setdefault(e.table, []).append(e)
    for page in sorted({title.page for title in titles.values()}):
        tables = sorted(t for t, title in titles.items() if title.page == page)
        refs = [{e.ref for e in by_table.get(t, [])} for t in tables]
        if len(tables) < _MIN_TITLED_BUILDS or not all(refs):
            continue
        shared = set.intersection(*refs)
        if len(shared) < _VARIANT_SHARE * max(len(r) for r in refs):
            continue
        names = {t: titles[t].name for t in tables}
        after = sorted(
            t
            for t, es in by_table.items()
            if t > tables[-1] and t not in titles and es[0].page == page + 1
        )
        if len(after) == len(tables):
            names |= {t: names[k] for t, k in zip(after, tables, strict=True)}
        entries = [
            replace(e, column=names[e.table]) if e.table in names else e
            for e in entries
        ]
    return entries


# 'Parts (vintage / modern values)' (Aion FX legacy Comet): the page's 'A / B'
# values are two builds'.
_SLASH_VERSIONS = re.compile(r"\((\w+) / (\w+) values\)", re.IGNORECASE)
# Another version's value beside the first: '1M', '470k', 'jumper', 'omit'.
_SECOND_VALUE = re.compile(r"\d[\w.]*|omit|jumper", re.IGNORECASE)
# 'IC1 (vintage)': a part in one build only; '1N914 (modern only)' likewise.
_VERSION_REF = re.compile(r"(\S+) \((\w+)\)")


_VERSION_ONLY = re.compile(r"(.+?) \((\w+) only\)", re.IGNORECASE)
# A 'Mod 1 | Mod 2' heading over tables of value changes (Aion FX legacy Comet).
_MOD_HEADING = re.compile(r"^\s*Mod 1\b", re.MULTILINE)
_CHANGE_LIST_MIN = 3


def _version_split(
    ref: str, value: str, versions: tuple[str, str] | None
) -> list[tuple[str, str, str | None]]:
    """Splits a row of a two-build list into each build's part.

    Under 'Parts (vintage / modern values)', '470n / 68n' is C4 in each build,
    'IC1 (vintage)' and '1N914 (modern only)' are parts of one build, and any
    other row is common (version None).
    """
    if versions is None:
        return [(ref, value, None)]
    lowered = [v.lower() for v in versions]
    named = _VERSION_REF.fullmatch(ref)
    if named and named.group(2).lower() in lowered:
        return [
            (named.group(1), value, versions[lowered.index(named.group(2).lower())])
        ]
    only = _VERSION_ONLY.fullmatch(value)
    if only and only.group(2).lower() in lowered:
        return [(ref, only.group(1), versions[lowered.index(only.group(2).lower())])]
    pair = value.split(" / ")
    if len(pair) == len(versions):
        return [(ref, v.strip(), name) for v, name in zip(pair, versions, strict=True)]
    return [(ref, value, None)]


# Lower case only: PedalPCB's 'TOGGLE SWITCH' is the part's whole name.
_RULED_LABEL_NOUN = re.compile(r"(?<=[A-Za-z]{3}) (?:switch|pot|trimmer)$")
# 'IC1 & IC2'; 'LED + *CLR' (GuitarPCB The Mule: a status LED and its CLR).
_REF_PAIR = re.compile(r"(\w+)\s*(?:&|\+|\band\b)\s*\*?(\w+)", re.IGNORECASE)
# 'RX2 (by IC1)': where on the board a position is.
_POSITION_ASIDE = re.compile(r"([A-Za-z]{1,4}\d{1,3})\s*\(by [^()]+\)")
# 'TR1 (see text)', 'TR2 (Volume)'; not 'RX2 (by IC1)' (`_POSITION_ASIDE`) or
# 'IC1 (vintage)' (Comet legacy names a build).
_REF_ASIDE = re.compile(r"([A-Za-z]{1,4}\d{1,3})\s*\(([^\d()]*)\)", re.DOTALL)


def _alternate_spec_table(
    table: list[list[str | None]], listed: list[Entry], stats: ReadingStats
) -> bool:
    """Reads a table restating listed parts' values beside other values.

    Aion FX legacy Refractor prints 'R10 | 2k | 47R | More gain' for the
    'Silver Pony' specs: every designator is already listed, the first value
    is the listed one and the second another version's. Such a table adds no
    parts; each part it gives another value is warned.

    Returns:
        Whether the table was such a list (and should not be read as parts).
    """
    values = {e.ref: e.value for e in listed}
    rows = [[str(c or "").strip() for c in row] for row in table]
    rows = [r for r in rows if r and r[0]]
    # 'R4 200k | | 180k /220k' (GuitarPCB G-02 Animal): the first two cells run
    # together.
    rows = [
        [*r[0].split(" ", 1), *r[2:]] if len(r) >= 3 and not r[1] and " " in r[0] else r
        for r in rows
    ]
    if len(rows) < _CHANGE_LIST_MIN or any(len(r) < 3 for r in rows):
        return False
    changes: list[tuple[str, str, str]] = []
    for row in rows:
        refs = expand_refs(row[0].replace(" ", ""))
        old, new = row[1].split(" / "), row[2].split(" / ")
        if not all(ref in values for ref in refs) or len(old) != len(new):
            return False
        if len(old) == 1:
            old, new = old * len(refs), new * len(refs)
        if len(old) != len(refs):
            return False
        for ref, was, now in zip(refs, old, new, strict=True):
            if was.split()[:1] != values[ref].split()[:1]:
                return False
            changes.append((ref, was, now))
    for ref, was, now in changes:
        # A remark ('Lower Value -Higher Gain', 'LED CLR') offers no value.
        if now.split()[:1] != was.split()[:1] and now[:1].isdigit():
            check_alternate_value(stats, ref, was, now)
    return True


def _is_change_list(table: list[Entry], seen: set[str], page_text: str) -> bool:
    """Whether a table under a 'Mod 1 | Mod 2' heading only re-lists earlier parts.

    Such a table gives other values for parts already listed ('IC1 OPA2134',
    'C1 100n'): a modification, not more parts to buy.
    """
    return (
        len(table) >= _CHANGE_LIST_MIN
        and bool(_MOD_HEADING.search(page_text))
        and all(e.ref in seen for e in table)
    )


# The designator column of a value-column grid: 'Part', 'Part #', "MUFF'N Part #",
# 'Component' (Five Cats OKS Fuzz).
_GRID_REF_HEADER = re.compile(r"(?:^|\s)(?:PART\s*#?|COMPONENT)$", re.IGNORECASE)
# Headers that name a column's role, not a circuit version.
_ROLE_HEADERS = frozenset(
    {
        "VALUE",
        "VALUES",
        "VAL",
        "TYPE",
        "NOTES",
        "NOTE",
        "QTY",
        "QUANTITY",
        "DESCRIPTION",
        "FOOTPRINT",
    }
)
# 'P1-Sus/Fuzz': the designator, then the control's name.
_NAMED_REF = re.compile(r"([A-Z]{1,3}\d{1,3})[-=](?![A-Z]{1,3}\d)[A-Za-z]")
# A note in a value column naming the row's role ('R12 | 1k8 | CLR', RATT).
_ROLE_CELLS = frozenset({"CLR"})


def _grid_header_text(cell: str | None) -> str:
    """A header cell as one line; a word wrapped mid-word ('Origina' / 'l') is rejoined."""
    lines = [line.strip() for line in str(cell or "").splitlines() if line.strip()]
    text = ""
    for line in lines:
        text += line if text and len(line) <= 2 else f" {line}"
    return text.strip()


def names_builds(header: list[str]) -> bool:
    """Whether a header row heads a value-column grid.

    It names a designator column, then two or more builds, not roles ('Value',
    'Type').
    """
    names = header[1:]
    return (
        bool(header)
        and _GRID_REF_HEADER.search(header[0]) is not None
        and len([n for n in names if n]) >= 2
        and not any(n.upper() in _ROLE_HEADERS for n in names)
    )


# A wide, headerless grid of 'Part | Value' pairs.
_SHADED_MIN_COLUMNS = 6
_JFET_PART = re.compile(r"J\d{3}")
_BUY_COUNT = re.compile(r"^([A-Z]{2,4}) x ?\d+\b")


_SPACED_RANGE = re.compile(r"\b([A-Z]{1,3}\d{1,3}) ?[-\u2013] ?([A-Z]{1,3}\d{1,3})\b")
_SHADED_MIN_PAIRS = 8


def _shaded_pairs(table: list[list[str | None]]) -> list[list[str]] | None:
    """Re-pairs a headerless 'Part | Value' grid whose shaded rows merge cells.

    GuitarPCB SWAH V4 prints its parts as side-by-side pairs with no header;
    the ruled reading splits the white rows into 'R9 | 4k7' but merges the
    shaded ones into 'R8 6k8 | None', so the columns do not line up and only
    the first pair of each row would be read. Each row is re-read as its
    sequence of pairs: a label cell then its value cell, or one cell holding
    both. A named control's designator keys it ('P1-Reso' is P1).

    Returns:
        One [label, value] row per pair, or None when the table is not such a grid.
    """
    if max((len(r) for r in table), default=0) < _SHADED_MIN_COLUMNS:
        return None

    def label(text: str) -> str | None:
        named = _NAMED_REF.match(text)
        if named and " " not in text:
            return named.group(1)
        if _JFET_PART.fullmatch(text) or text.upper() == "TRIM":
            return None  # 'J201' is the JFET, not jack J201; 'Trim 50K' a trimmer
        if REF_RANGE.fullmatch(text) or is_designator(text):
            return text
        pair = _REF_PAIR.fullmatch(text)
        if pair and all(is_designator(g) for g in pair.groups()):
            return ",".join(pair.groups())  # 'LED + *CLR | Status' (The Mule)
        words = text.replace(",", " ").split()
        if len(words) > 1 and all(is_designator(w) for w in words):
            return ",".join(words)  # 'D1 D2 | 1N4148' (GuitarPCB Fat Cat Fuzz)
        # 'TONE | B10k' (GuitarPCB Anxiety Disorder): a control's name.
        if text.isupper() and names_control(text):
            return text
        return None

    def holds_pair(text: str) -> bool:
        if label(text) is not None:
            return False  # two designators sharing one value cell
        head, _, rest = text.partition(" ")
        return bool(rest) and label(head) is not None

    def starts_pair(cells: list[str], k: int) -> bool:
        # A bare label starts a pair only with a value after it: 'D1-D2 | LED'
        # pairs the range with the value 'LED', not with an LED designator.
        if holds_pair(cells[k]):
            return True
        after = cells[k + 1] if k + 1 < len(cells) else None
        return (
            label(cells[k]) is not None
            and after is not None
            and label(after) is None
            and not holds_pair(after)
        )

    def stray(cells: list[str], k: int) -> bool:
        """A lone value after a pair: its rating, or another build's value."""
        text = cells[k]
        return RATING_CELL.fullmatch(text) is not None or (
            text[:1].isdigit() and " " not in text and not starts_pair(cells, k)
        )

    pairs: list[list[str | None]] = []
    for row in table:
        # '** Q2-Q6', 'D1 - D2' (GuitarPCB NostalgiTone 70s).
        # '* CLR x3': how many to buy is no value. A cell merged over two rows
        # ('P2 TONE 10k Lin' / 'P3 VOL 500k Log', GuitarPCB Over Drive Circuit)
        # is two cells.
        texts: list[str] = []
        for c in row:
            if not c or not c.replace("*", "").strip():
                continue
            parts = [ln for ln in c.split("\n") if ln.strip()]
            if len(parts) > 1 and all(holds_pair(ln) for ln in parts):
                texts += parts
            else:
                texts.append(c)
        cells = [
            _BUY_COUNT.sub(
                r"\1",
                _SPACED_RANGE.sub(r"\1-\2", " ".join(t.replace("*", " ").split())),
            )
            for t in texts
        ]
        k = 0
        while k < len(cells):
            head, _, rest = cells[k].partition(" ")
            whole = label(cells[k])
            if (
                whole
                and k + 2 < len(cells)
                and names_control(cells[k + 1])
                and not starts_pair(cells, k + 2)
                and label(cells[k + 2]) is None
            ):
                # 'P2 | VOL 2 | 100k Log' (GuitarPCB XX Double Shot).
                pairs.append([whole, cells[k + 2]])
                k += 3
                continue
            if whole and k + 1 < len(cells) and not starts_pair(cells, k + 1):
                value, k = cells[k + 1], k + 2
                digits, _, tail = value.partition(" ")
                if digits.isdigit() and tail and is_designator(whole + digits):
                    # 'R1 | 1 2k2' (GuitarPCB Bass Overthruster): the cell
                    # boundary split 'R11'.
                    whole, value = whole + digits, tail
                # 'C4 | 47n | 63V' (GuitarPCB DVF v3): the rating has a cell;
                # 'R5 | 10k | 470k' (older Tube Screaming Ultra) another build's
                # value, kept so the two values are warned.
                if k < len(cells) and stray(cells, k):
                    value, k = f"{value} {cells[k]}", k + 1
                pairs.append([whole, value])
            elif rest and label(head):
                after = cells[k + 1] if k + 1 < len(cells) else None
                if names_control(rest) and after and not starts_pair(cells, k + 1):
                    # 'P1 GAIN | 1M Log': the control's name, then its value.
                    pairs.append([label(head), after])
                    k += 2
                    continue
                name, _, tail = rest.partition(" ")
                if tail and names_control(name):
                    rest = tail  # 'P2 TONE 10k Lin': the control's name, then its value
                k += 1
                if k < len(cells) and stray(cells, k):
                    rest, k = f"{rest} {cells[k]}", k + 1
                pairs.append([label(head), rest])
            else:
                k += 1
    if len(pairs) < _SHADED_MIN_PAIRS:
        return None
    # 'D1-D4 | 1N914' is four rows, so each designator is warned on its own.
    return [
        [ref, value]
        for refs, value in pairs
        if refs and value
        for ref in (expand_refs(refs) if REF_RANGE.fullmatch(refs) else [refs])
    ]


def _value_column_grid(
    table: list[list[str | None]], page_no: int, table_no: int
) -> list[Entry] | None:
    """Reads a parts table with one value column per circuit version.

    GuitarPCB's MUFF'N prints 'MUFF'N Part # | Ram | Violet Ram | Triangle ...'
    and RATT Deluxe 'Part # | GPCB Values | Original': each row is a designator
    and its value in every version. An empty cell keeps the first column's
    value (RATT's original-Rat column leaves the board's added switches blank).

    Returns:
        One entry per designator and column, tagged with the column's name, or
        None when the table is not such a grid.
    """
    header_rows = [
        (k, cells)
        for k, row in enumerate(table[:3])
        if (cells := [_grid_header_text(c) for c in row])
        and _GRID_REF_HEADER.search(cells[0])
    ]
    if not header_rows:
        return None
    start, cells = header_rows[0]
    if not names_builds(cells):
        return None
    names = cells[1:]
    entries: list[Entry] = []
    for row in table[start + 1 :]:
        # 'FILTE' / 'R' is a wrapped 'FILTER' (RATT Deluxe).
        ref = _grid_header_text(row[0]).replace("*", "")
        named = _NAMED_REF.match(ref)
        if named:
            ref = named.group(1)
        if ref.istitle() and ref.isalpha():
            ref = ref.upper()  # 'Pot' (Five Cats OKS Fuzz, read by OCR)
        if not ref or not (
            any(is_designator(r) for r in expand_refs(ref))
            or CONTROL_LABEL.fullmatch(ref)
        ):
            continue
        cells = [" ".join(str(c or "").replace("*", " ").split()) for c in row[1:]]
        if any(c.split()[:1] == [ref] for c in cells if c):
            # A remark spanning the row: 'R24 | R24 is only available on v3 PCB
            # or earlier | omit ...' (MUFF'N).
            continue
        first = ""
        for k, name in enumerate(names):
            if not name:
                continue
            cell = cells[k] if k < len(cells) else ""
            if k == 0:
                first = cell
            elif not cell or cell.upper() in _ROLE_CELLS:
                cell = first
            if not cell or len(cell.split()) > 4:
                # Blank, or a remark spanning the row ('R24 is only available
                # on v3 PCB or earlier'): no part in this version.
                continue
            entries.append(Entry(ref, cell, page_no, table=table_no, column=name))
    return entries
