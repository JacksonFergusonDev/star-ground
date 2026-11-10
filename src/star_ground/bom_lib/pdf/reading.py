"""Reads a document's parts lists from its pages and checks the result.

The same readers serve a PDF's text layer and OCR'd page images: both
arrive as positioned words (`PDFPageExtraction`).
"""

from dataclasses import dataclass, replace

from star_ground.bom_lib.builds import Entry, PageContext, plan_builds
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.pdf.assemble import (
    assemble_builds,
    included_refs,
    mark_included,
)
from star_ground.bom_lib.pdf.geometry import page_lines
from star_ground.bom_lib.pdf.listings import layout_entries
from star_ground.bom_lib.pdf.rows import REPEAT, same_value, wrapped
from star_ground.bom_lib.pdf.ruled import (
    SHADED_LINE,
    continues_list,
    fill_in,
    table_entries,
)
from star_ground.bom_lib.pdf.self_check import ReadingFacts, check_reading
from star_ground.bom_lib.pdf.text_tables import text_table_entries
from star_ground.bom_lib.types import (
    BuildVariant,
    Inventory,
    OptionNote,
    PDFPageExtraction,
    ReadingStats,
)


def read_pages(
    pages_data: list[PDFPageExtraction],
    source_name: str,
    stats: ReadingStats,
    *,
    tables_only: bool = False,
) -> tuple[Inventory, tuple[BuildVariant, ...], tuple[OptionNote, ...]]:
    """Reads the parts lists of a document's pages and checks the result.

    The same readers serve a PDF's text layer and OCR'd page images (see
    `ocr.py`): both arrive as positioned words.

    Args:
        pages_data: Every page's tables, text and words, in document order.
        source_name: Label for the source.
        stats: Statistics and warnings, updated in place.
        tables_only: Whether each page holds nothing but its parts tables (OCR
            keeps only those), so every page is read, not only those with a
            text bill of materials.

    Returns:
        The single-build inventory (empty when the document has variants),
        the variants, and the option notes.
    """
    found = read_entries(pages_data, stats, tables_only=tables_only)

    # --- BUILDS: variants and options ---
    pages = [
        PageContext(
            page_lines(data.words),
            data.text,
            tuple(data.labels),
        )
        for data in pages_data
    ]
    plan = plan_builds(found.entries, pages)
    inventory, variants, options, union = assemble_builds(plan, source_name, stats)
    supplied = included_refs([data.text for data in pages_data])
    for parts in (inventory, *(v.inventory for v in variants)):
        mark_included(parts, supplied)

    check_reading(
        ReadingFacts(
            pages_data,
            found.entries,
            found.bom_pages,
            union,
            variants,
            tables_only=tables_only,
        ),
        stats,
    )
    return inventory, variants, options


@dataclass(frozen=True, slots=True)
class PageEntries:
    """What the readers found on a document's pages.

    Attributes:
        entries: Every row read, by every reader, each once.
        bom_pages: The pages holding a text bill of materials, to which the
            other readers keep; None when no page does.
    """

    entries: list[Entry]
    bom_pages: set[int] | None


def read_entries(
    pages_data: list[PDFPageExtraction],
    stats: ReadingStats,
    *,
    tables_only: bool = False,
) -> PageEntries:
    """Runs every reader over the pages and merges what they read.

    Tables are read first (ruled, then text tables); the layout reader then
    recovers what the tables missed, or everything, for column layouts.

    Args:
        pages_data: Every page's tables, text and words, in document order.
        stats: Statistics and warnings, updated in place.
        tables_only: Whether each page holds nothing but its parts tables.

    Returns:
        The rows read, and the pages holding a text bill of materials.
    """
    # --- STRATEGY 1: TABLE EXTRACTION ---
    # A text bill of materials is the document's parts list, so the
    # other strategies only read its own pages: elsewhere (vector
    # schematics) symbol labels look like listings and drawings like
    # tables.
    text_rows, row_ends = text_table_entries(pages_data, stats)
    # Five Cats Tone Monarch heads its main table but not its daughterboard's.
    bom_pages = None if tables_only else ({row.page for row in text_rows} or None)
    if bom_pages:
        # A list that runs onto the next page in ruled tables alone (GuitarPCB
        # EMEXAR's 'GAIN | C500k' rows) has no text table there.
        bom_pages |= {
            p + 1
            for p in bom_pages
            if p + 1 < len(pages_data) and continues_list(pages_data[p + 1])
        }
    entries = table_entries(pages_data, stats, bom_pages)
    first_text_table = 1 + max(
        (e.table for e in entries if e.table is not None), default=-1
    )
    # Re-paired shaded grids only fill in for the other readers (see `fill_in`).
    shaded = [e for e in entries if e.line == SHADED_LINE]
    entries = [e for e in entries if e.line != SHADED_LINE]
    # A boxed 'Part | Value' grid is both a ruled table and a text table
    # (GuitarPCB); read it once, or every part would be bought twice.
    # The ruled cell keeps a value wrapped over two lines ('Bi-Color CA
    # LED (see' / 'text)', Mini-Me), so the ruled reading wins.
    # A repeated label is numbered by the text reading only ('MIX (2)',
    # Cataclysm lists MIX twice), so rows are matched by their label.
    # Another board's table on the page can list the same designator with its
    # own value (GuitarPCB Skyn-Tone: a ruled Compressor table above a text
    # WattAmp table); only the same row read twice is dropped.
    # But a text row with one value per build ('MXR Value | DOD Value', GuitarPCB
    # Emexar v4) keeps the builds apart, which the ruled cells run together.
    by_build = {(r.page, REPEAT.sub("", r.ref)) for r in text_rows if r.column}
    entries = [e for e in entries if (e.page, e.ref) not in by_build]
    ruled: dict[tuple[int, str], list[str]] = {}
    for e in entries:
        ruled.setdefault((e.page, e.ref), []).append(e.value)
    text_rows = [
        r
        for r in text_rows
        if not any(
            same_value(v, r.value) or wrapped(v, r.value)
            for v in ruled.get((r.page, REPEAT.sub("", r.ref)), ())
        )
    ]
    # Number the text tables after the ruled ones, so no text table shares
    # a ruled table's number (`assemble` would take another board's R1 for a
    # conflicting duplicate). A table's rows come together, so the first board
    # keeps its designators ('R1' before 'R1 (2)') however the boards' rows
    # interleave on the page (GuitarPCB DumbleTone).
    text_rows.sort(key=lambda row: (row.page, row.table or 0))
    entries += [
        replace(row, table=first_text_table + (row.table or 0)) for row in text_rows
    ]

    # --- STRATEGY 2: LAYOUT ANALYSIS ---
    # Recovers anything the tables missed (or everything, for column layouts)
    layout = layout_entries(
        pages_data, stats, {e.ref: e.value for e in entries}, bom_pages, row_ends
    )
    # A ruled cell's wrapped lines side by side read like a listing: GuitarPCB
    # Disturbance's 'Bi-Color / CA LED*' beside 'SPDT / On-Off-On' gives 'CA LED
    # | On-Off-On', which is no part.
    cells_on = {
        page: [v for (p, _), vs in ruled.items() if p == page for v in vs]
        for page in {e.page for e in layout}
    }
    entries += [
        e
        for e in layout
        if is_designator(e.ref)
        or not any(wrapped(v, e.ref.rstrip("*")) for v in cells_on[e.page])
        or not any(wrapped(v, e.value) for v in cells_on[e.page])
    ]
    return PageEntries(fill_in(entries, shaded), bom_pages)
