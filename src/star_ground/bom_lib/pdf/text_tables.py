"""Reads unruled 'Part | Value | Type' bills of materials from positioned words."""

import re
from collections import Counter
from dataclasses import replace
from itertools import islice

from star_ground.bom_lib import constants
from star_ground.bom_lib.builds import Entry
from star_ground.bom_lib.classifier import (
    categorize_part,
)
from star_ground.bom_lib.designators import is_designator, parse_designator
from star_ground.bom_lib.enrichment import (
    POLE_THROW,
    capacitor_notes,
    category_from_type,
    drop_restated_value,
    enrich_value_from_type,
)
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.pdf.bom_table import bom_note_cells, find_bom_rows
from star_ground.bom_lib.pdf.cells import ref_group
from star_ground.bom_lib.pdf.geometry import group_lines
from star_ground.bom_lib.pdf.rows import (
    CONTROL_LABEL,
    SOCKET,
    is_not_populated,
    next_label,
)
from star_ground.bom_lib.types import (
    PDFPageExtraction,
    ReadingStats,
)
from star_ground.bom_lib.utils import expand_refs
from star_ground.bom_lib.verification import (
    check_build_note,
    check_marking_code,
    check_part_versions,
    check_row_quantity,
    check_unclassified_row,
    check_version_note,
)

# The first of a list of interchangeable parts: '(7530, 7532, 9203, or similar)'.
_NOTES_CHOICE = re.compile(r"\(?\s*([A-Z]*\d[\w./-]*)\s*,")


_TABLE_HEADERS = frozenset({"LOCATION", "QUANTITY", "VALUE", "TYPE"})


def typed_row(
    stats: ReadingStats,
    ref: str,
    value: str,
    type_text: str,
    notes: str,
    *,
    check: bool = True,
) -> tuple[str, ComponentCategory | None]:
    """Completes a typed table row's value from its TYPE and NOTES cells.

    Shared by ruled and text tables (PedalPCB's 'LOCATION | VALUE | TYPE |
    NOTES', Aion FX's 'PART | VALUE | TYPE | NOTES').

    Args:
        stats: Stats object that receives warnings.
        ref: The designator cell.
        value: The VALUE cell.
        type_text: The TYPE cell.
        notes: The NOTES cell, if the table has one.
        check: Whether to report a row no category fits; text tables check
            each designator of a grouped cell themselves.

    Returns:
        The enriched value and the category the TYPE cell names, if any.
    """
    type_text = " ".join(type_text.split())
    # ElectroVibe: 'LDR1 | (blank) | Light Dependent Resistor |
    # (7530, 7532, 9203, or similar)' names the part in NOTES.
    choice = _NOTES_CHOICE.match(notes.strip())
    if not value and choice:
        value = choice.group(1)
    # Lone King: 'VOICE | (blank) | Toggle switch, SPDT | 3-position
    # (ON/OFF/ON)'; enrichment then adds the positions.
    pole = POLE_THROW.search(type_text.upper())
    if not value and pole:
        value = pole.group(0)
    # Kickstart: 'SW1 | (blank) | 6mm x 6mm Push Button Switch'.
    if not value and type_text:
        value = type_text
    value = enrich_value_from_type(value, type_text, notes)
    hint = category_from_type(type_text)
    if SOCKET.search(value) and not SOCKET.search(type_text):
        # Aion FX Nysiad Silicon: 'IC1-S | DIP-8 socket | BJT transistor, NPN,
        # TO-92' repeats the row above's TYPE; a socket is hardware.
        hint = ComponentCategory.HARDWARE_MISC
    if check:
        _check_typed_row(stats, ref, value, hint)
    return value, hint


def _check_typed_row(
    stats: ReadingStats, ref: str, value: str, hint: ComponentCategory | None
) -> None:
    """Reports a typed table row that no category fits (it must not vanish)."""
    if (
        ref[:1].isalpha()
        and value
        and value.upper() not in constants.NOT_POPULATED_VALUES
        # A repeated or garbled header row is not a part.
        and not {ref.upper(), value.upper()} & _TABLE_HEADERS
        and categorize_part(ref, value, hint) is None
    ):
        check_unclassified_row(stats, ref, value)


def text_table_entries(
    pages_data: list[PDFPageExtraction], stats: ReadingStats
) -> tuple[list[Entry], dict[tuple[int, int], float]]:
    """Strategy 1b: Extract entries from unruled 'Reference | Value | Quantity' tables.

    Each row may list several designators ('R10-R12,R18'); it becomes one
    entry per designator. A quantity that disagrees with the designator count
    is flagged, since one of the two must be wrong.

    Args:
        pages_data: Pre-extracted page data including word positions.
        stats: The statistics dictionary for tracking progress.

    Returns:
        One entry per designator, in reading order, and for each (page, line)
        read as a row, where the row ends (see `BomRow.end`). Tables are
        numbered from 0 in page order, and by header line within a page, so
        two tables on one page stay apart.
    """
    entries: list[Entry] = []
    spans: dict[tuple[int, int], float] = {}
    seen: Counter[str] = Counter()
    noted: list[tuple[str, str, str]] = []
    for page_no, data in enumerate(pages_data):
        for row in find_bom_rows(data.words):
            stats.lines_read += 1
            noted.append((row.refs, row.value, row.notes))
            value, hint = row.value, None
            group = ref_group(row.refs)  # 'LDR 1 - 4' (GuitarPCB Band of Gypsys)
            if group is not None:
                refs = expand_refs(group)
            else:
                refs = expand_refs(row.refs) if " " not in row.refs else [row.refs]
            # 'VALUE (D+) | VALUE (250)': one value per build (Aion FX Aphelion).
            builds: list[tuple[str | None, str]] = list(row.builds) or [(None, value)]
            if row.type_text:
                typed: list[tuple[str | None, str]] = []
                for build, cell in builds:
                    cell, hint = typed_row(
                        stats, row.refs, cell, row.type_text, row.notes, check=False
                    )
                    cell = drop_restated_value(cell)
                    typed.append((build, capacitor_notes(row.refs, cell, row.notes)))
                if hint is None and not all(is_designator(ref) for ref in refs):
                    # A label the TYPE does not explain ('Screw | M3 x 5mm | For
                    # Nylon', Q-Tune's packing list) is left to the layout reader.
                    continue
                builds = typed
                value = builds[0][1]
                # An empty spot can be filled in another build ('(omit) |
                # 100n for bass'), so its note matters too.
                check_version_note(stats, row.refs, row.notes)
                if row.builds:
                    check_build_note(
                        stats, row.refs, [c for _, c in row.builds], row.notes
                    )
                for ref in refs:
                    _check_typed_row(stats, ref, value, hint)
            elif row.notes and is_not_populated(value):
                # '(omit) | | Formula 5F6: 4.7uF electrolytic' (Aion FX Triton):
                # an empty spot with no TYPE is filled in another build.
                check_version_note(stats, row.refs, row.notes)
            if hint is not None:
                # A typed table describes every row, so a repeated label is
                # another part (Aion FX: GAIN pot and GAIN switch, Vulcan's
                # two R9s); it is bought too, under 'GAIN (2)'.
                refs = [next_label(seen, ref) for ref in refs]
            check_row_quantity(stats, row.refs, len(refs), row.quantity)
            if hint is None:
                refs = _untyped_refs(stats, refs, value)
            for ref in refs:
                designator = parse_designator(ref)
                if designator and designator.category == ComponentCategory.CAPACITORS:
                    check_marking_code(stats, ref, value, row.marking)
            if refs:
                spans[page_no, row.line] = row.end
            entries += [
                Entry(
                    ref,
                    cell,
                    page_no,
                    line=row.line,
                    x0=row.x0,
                    table=row.table,  # numbered across pages below
                    hint=hint,
                    column=build,
                )
                for build, cell in builds
                for ref in refs
            ]
    check_part_versions(stats, noted)
    tables = sorted({(e.page, e.table) for e in entries if e.table is not None})
    number = {table: n for n, table in enumerate(tables)}
    entries = [
        replace(e, table=number[e.page, e.table]) if e.table is not None else e
        for e in entries
    ]
    return entries, spans


def _untyped_refs(stats: ReadingStats, refs: list[str], value: str) -> list[str]:
    """The parts of an untyped table row: designators and named controls.

    A 'Part | Value' grid lists controls beside designators ('VOL | A100k',
    GuitarPCB), which their value classifies. An odd name grouped with real
    designators ('R8, RPDo', Q-Tune) is reported; other labels (titles,
    footers, hardware such as 'Screw') are left to the layout reader.
    """
    if any(is_designator(ref) for ref in refs):
        for odd in [ref for ref in refs if not is_designator(ref)]:
            check_unclassified_row(stats, odd, value)
        return [ref for ref in refs if is_designator(ref)]
    return [
        ref
        for ref in refs
        if CONTROL_LABEL.fullmatch(ref) and categorize_part(ref, value) is not None
    ]


def without_note_cells(data: PDFPageExtraction, text: str) -> str:
    """Page text with typed tables' NOTES cells removed (see `bom_note_cells`).

    Each cell is removed where it sits, not wherever its text first appears:
    a NOTES cell 'C2' is the page's nth whole-word 'C2' if n - 1 come before
    it in reading order, so the 'C2' of an earlier 'C20' row, or the
    designator of the row it notes, stays.
    """
    cells = bom_note_cells(data.words)
    if not cells:
        return text
    # The page's words in reading order, as the text runs, and where each starts.
    starts: dict[int, int] = {}
    stream: list[str] = []
    offset = 0
    for line in group_lines(data.words):
        for word in line:
            starts[id(word)] = offset
            stream.append(word["text"])
            offset += len(word["text"]) + 1
    words_text = " ".join(stream)
    spans: set[tuple[int, int]] = set()
    for cell in cells:
        start = starts.get(id(cell.words[0]))
        if start is None:
            continue
        pattern = re.compile(rf"(?<!\S){re.escape(cell.text)}(?!\S)")
        before = sum(1 for _ in pattern.finditer(words_text, 0, start))
        found = next(islice(pattern.finditer(text), before, None), None)
        if found is not None:
            spans.add(found.span())
    for begin, end in sorted(spans, reverse=True):
        text = f"{text[:begin]} {text[end:]}"
    return text
