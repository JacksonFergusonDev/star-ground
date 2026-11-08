"""Reads a document's parts lists from its pages and checks the result.

The same readers serve a PDF's text layer and OCR'd page images: both
arrive as positioned words (`PDFPageExtraction`).
"""

from collections import Counter
from dataclasses import replace

from star_ground.bom_lib.builds import PageContext, plan_builds
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.enums import ParseWarningKind
from star_ground.bom_lib.pdf.assemble import (
    assemble_builds,
    included_refs,
    mark_included,
)
from star_ground.bom_lib.pdf.bom_table import bom_valueless_refs
from star_ground.bom_lib.pdf.geometry import page_lines
from star_ground.bom_lib.pdf.listings import layout_entries
from star_ground.bom_lib.pdf.page_checks import (
    pictured_lists,
    unreadable_rows,
    valueless_refs,
)
from star_ground.bom_lib.pdf.rows import REPEAT, is_not_populated, same_value, wrapped
from star_ground.bom_lib.pdf.ruled import (
    SHADED_LINE,
    continues_list,
    fill_in,
    table_entries,
)
from star_ground.bom_lib.pdf.scan import find_listings
from star_ground.bom_lib.pdf.text_tables import text_table_entries, without_note_cells
from star_ground.bom_lib.types import (
    BuildVariant,
    Inventory,
    OptionNote,
    PDFPageExtraction,
    ReadingStats,
)
from star_ground.bom_lib.utils import expand_refs
from star_ground.bom_lib.verification import (
    check_bom_reconciliation,
    check_build_changes,
    check_chip_sets,
    check_either_part,
    check_footnotes,
    check_numbered_footnotes,
    check_numbering_gaps,
    check_other_list,
    check_parts_found,
    check_pictured_lists,
    check_prose_pots,
    check_quantity_list,
    check_two_values,
    check_unextracted_refs,
    check_unreadable_rows,
    check_value_assignments,
    check_value_keyed_list,
    check_valueless_refs,
    is_schematic_page,
)

# A page outside the parts list with this many listings, this many starting
# at one x position, holds another parts list (GuitarPCB Stage 3's page 5).
_OTHER_LIST_MIN = 8


_OTHER_LIST_ALIGNED = 4


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
    # Number the text tables after the ruled ones. A table's rows come
    # together, so the first board keeps its designators ('R1' before 'R1 (2)')
    # however the boards' rows interleave on the page (GuitarPCB DumbleTone).
    text_rows.sort(key=lambda row: (row.page, row.table or 0))
    entries += [
        replace(row, table=len(entries) + (row.table or 0)) for row in text_rows
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
    entries = fill_in(entries, shaded)

    # --- BUILDS: variants and options ---
    pages = [
        PageContext(
            page_lines(data.words),
            data.text,
            tuple(data.labels),
        )
        for data in pages_data
    ]
    plan = plan_builds(entries, pages)
    inventory, variants, options, union = assemble_builds(plan, source_name, stats)
    supplied = included_refs([data.text for data in pages_data])
    for parts in (inventory, *(v.inventory for v in variants)):
        mark_included(parts, supplied)

    # --- SELF-CHECK ---
    # Every designator listed in the document must be accounted for, in
    # some build or option.
    page_texts = [data.text for data in pages_data]
    # Like the readers, a text bill of materials bounds where parts are
    # listed: Q-Tune's build steps mention 'the X1 connector area'.
    listed_texts = [
        without_note_cells(data, text) if bom_pages is None or k in bom_pages else ""
        for k, (data, text) in enumerate(zip(pages_data, page_texts, strict=True))
    ]
    check_parts_found(
        stats, sum(len(d.refs) + sum(d.counted.values()) for d in union.values())
    )
    check_value_keyed_list(page_texts, stats)
    counted = [e.ref.removeprefix("#") for e in entries if e.counted]
    check_quantity_list(page_texts, counted, stats.unread_counted, stats)
    omitted = frozenset(
        r for e in entries if is_not_populated(e.value) for r in expand_refs(e.ref)
    )
    check_unextracted_refs(listed_texts, union, stats, omitted)
    # A build's list on its own page (Aion FX legacy Cerulean: 'DX1-2  ²' in the
    # stock list, '1S1588' in King of Tone's) leaves a part empty although
    # another build fills it.
    read_on: dict[int, set[str]] = {}
    for e in entries:
        read_on.setdefault(e.page, set()).update(
            r.upper() for r in expand_refs(REPEAT.sub("", e.ref))
        )
    check_valueless_refs(
        [
            ref
            for k, data in enumerate(pages_data)
            if bom_pages is None or k in bom_pages
            for ref in [
                *valueless_refs(data.words),
                *bom_valueless_refs(data.words),
            ]
            if not variants or ref.upper() not in read_on.get(k, set())
        ],
        union if not variants else Inventory(),
        stats,
    )
    if tables_only:
        check_unreadable_rows(
            [row for data in pages_data for row in unreadable_rows(data.words)],
            stats,
        )
    check_footnotes(page_texts, stats)
    check_numbered_footnotes(
        [data.marked for data in pages_data], page_texts, union, stats
    )
    # A document read as several builds may describe one more in its notes
    # (Aion FX legacy Aurora: Ross and Dyna Comp columns, then the JangleBox's
    # replacements); a part both read builds give is warned already.
    check_build_changes(page_texts, union, stats, omitted)
    check_value_assignments(page_texts, union, stats)
    check_prose_pots(page_texts, union, stats)
    check_chip_sets(page_texts, union, stats)
    check_numbering_gaps(union, stats)
    check_two_values(union, stats)
    check_either_part(page_texts, union, stats)
    pictured = {
        k: found
        for k, data in enumerate(pages_data)
        if (found := pictured_lists(data.words, data.pictures))
    }
    refs_by_page: dict[int, set[str]] = {}
    for e in entries:
        refs_by_page.setdefault(e.page, set()).add(REPEAT.sub("", e.ref))
    check_pictured_lists(pictured, refs_by_page, stats)
    if bom_pages is not None:
        read: dict[str, str] = {}
        for e in entries:
            read.setdefault(e.ref, e.value)
        for k, data in enumerate(pages_data):
            # A schematic's 'REF value' labels are a drawing (Aion FX Procyon).
            if k in bom_pages or is_schematic_page(data.text):
                continue
            listings = find_listings(data.words)
            columns = Counter(round(listing.x0) for listing in listings)
            # A table, not labels scattered over a schematic or board render.
            if len(listings) >= _OTHER_LIST_MIN and max(columns.values()) >= (
                _OTHER_LIST_ALIGNED
            ):
                check_other_list(
                    k, [(li.ref, li.value) for li in listings], read, stats
                )
    check_bom_reconciliation(page_texts, union, stats)
    if counted or any(w.kind == ParseWarningKind.QUANTITY_LIST for w in stats.warnings):
        # A list that counts parts ('3 - 1N914') names no designators for them,
        # so the schematic's D1-D3 are 'missing' by design: the parts were read
        # by count, or the document is told they were not read.
        stats.warnings[:] = [
            w for w in stats.warnings if w.kind != ParseWarningKind.UNEXTRACTED_REF
        ]
    return inventory, variants, options
