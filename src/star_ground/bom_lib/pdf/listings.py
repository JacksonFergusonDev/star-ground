"""Reads parts lists laid out as text rather than tables.

These are 'R1 10k' listings in columns, and lists that count parts by value
('3 - 1N914').
"""

import re

from star_ground.bom_lib import constants
from star_ground.bom_lib.builds import Entry
from star_ground.bom_lib.classifier import (
    categorize_part,
    counted_category,
)
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.ingest import POINTER_VALUE
from star_ground.bom_lib.pdf.cells import (
    REF_RANGE,
    clean_value,
    control_designator,
    names_control,
)
from star_ground.bom_lib.pdf.counted import counted_parts, counted_rows
from star_ground.bom_lib.pdf.geometry import Cell, Listing
from star_ground.bom_lib.pdf.page import PDFPageExtraction
from star_ground.bom_lib.pdf.rows import RATING_CELL, is_ignored
from star_ground.bom_lib.types import (
    ReadingStats,
)
from star_ground.bom_lib.utils import expand_refs
from star_ground.bom_lib.verification import (
    check_unclassified_row,
)

# A control's name with its pole type: 'CLIP-SPDT'.
_POLES_SUFFIX = re.compile(r"([A-Z][A-Z0-9]+)-((?:[SD3]P[DS]T))", re.IGNORECASE)


_NUMBERED_NAME = re.compile(r"[A-Z]{1,3}\d{1,3}")


# Dashed listings that make a document's parts list dashed.
_DASHED_DOCUMENT = 8


# A switch's pads, as OCR reads 'A, B C' and 'D, E F': a letter or two.
_PAD_LETTERS = re.compile(r"[A-Z]{1,2}")


def layout_entries(
    pages_data: list[PDFPageExtraction],
    stats: ReadingStats,
    table_values: dict[str, str],
    only_pages: set[int] | None = None,
    row_ends: dict[tuple[int, int], float] | None = None,
) -> list[Entry]:
    """Strategy 2: Extract entries from the page layout.

    Reads word positions to pair each designator with the value printed in the
    neighboring column (see `scan.py`). Designators the table strategy already
    found are skipped, so the two strategies never disagree about a part.

    Args:
        pages_data: Pre-extracted page data including word positions.
        stats: The reading's counts and warnings, updated in place.
        table_values: Designators already extracted from tables, with their values.
        only_pages: If given, the only pages to read.
        row_ends: Lines already read as text-table rows, keyed (page, line),
            with where each row ends; listings beyond the end are read.

    Returns:
        One entry per listing, with its page position.
    """
    entries: list[Entry] = []
    found = {
        page_no: data.listings
        for page_no, data in enumerate(pages_data)
        if only_pages is None or page_no in only_pages
    }
    # A document whose parts list sets every listing with a dash ('R1 - 10k',
    # Tonepad), or counts parts the same way ('2 - 1k'), prints no part without one: a listing without one is a label of
    # its layout or schematic drawing.
    dashed = sum(
        listing.dashed for listings in found.values() for listing in listings
    ) + sum(counted_rows(pages_data[k].lines) for k in found)
    if dashed >= _DASHED_DOCUMENT:
        found = {
            page_no: [lst for lst in listings if lst.dashed]
            for page_no, listings in found.items()
        }
    for page_no, data in enumerate(pages_data):
        if page_no not in found:
            continue
        # A ruled table's rows are read by the table strategy; read as layout,
        # 'D1 | LED | 3mm Red LED' looks like a bare 'LED' listing (Bad Mamma
        # Jamma), and a wrapped 'FILTE / R' cell like an 'R' control (RATT).
        # But the table may have read only the first of the listings side by
        # side on a line ('R2 | 1M | C2 | 220n' with its shaded rows merged
        # into 'R1 1M', NostalgiTone), so numbered designators other than the
        # row's own value are still read there.
        lines = data.lines
        # Labels compare in upper case: a grid reads 'Pot' as 'POT'.
        upper_values = {ref.upper(): value for ref, value in table_values.items()}
        table_lines = {
            k: set(upper_values[cells[0].text.upper()].upper().split())
            for k, cells in enumerate(lines)
            if cells and cells[0].text.upper() in upper_values
        }
        for listing in found[page_no]:
            stats.lines_read += 1
            # A range lists each of its designators: 'D1-D2  1N4148'.
            refs = (
                expand_refs(listing.ref)
                if REF_RANGE.fullmatch(listing.ref) or "," in listing.ref
                else [control_designator(listing.ref) or listing.ref]
            )
            value = listing.value
            poles = _POLES_SUFFIX.fullmatch(listing.ref)
            if poles:
                # 'CLIP-SPDT  On/Off/On' (GuitarPCB Doom Prophet, read by OCR).
                refs, value = [poles.group(1)], f"{poles.group(2)} {value}"
            end = (row_ends or {}).get((page_no, listing.line), -1)
            for ref in refs:
                # A switch labeled by its pads is no wrapped cell, and a table
                # row does not read it: 'A, B C | SPDT On/Off/On' (GuitarPCB DVF,
                # read by OCR) beside 'R5 | 10k'.
                pad_label = bool(_PAD_LETTERS.fullmatch(ref)) and bool(
                    constants.SWITCH_VALUE_PATTERN.search(value.upper())
                )
                # A listing past the row's end is in a table beside it.
                row_value = (
                    None if listing.x0 >= end > 0 else table_lines.get(listing.line)
                )
                if (
                    ref.upper() in upper_values
                    # 'Q1-Q5' read whole by the table (GoldenFalk).
                    or listing.ref.upper() in upper_values
                    or (
                        row_value is not None
                        and not pad_label
                        and (
                            # A known control name is no wrapped cell ('SPEED |
                            # *500k' in a cell the table merged over two lines,
                            # GuitarPCB Dr Phybes).
                            (
                                not is_designator(ref)
                                and ref.upper() not in constants.KEYWORDS
                            )
                            or ref.upper() in row_value
                            # A designator with nothing beside it is kept, to be
                            # warned (GuitarPCB Capt Munch's D3, its value
                            # wrapped above and below the row).
                            or (value and not _cell_value(lines[listing.line], listing))
                        )
                    )
                    or (listing.x0 < end and not pad_label)
                ):
                    continue
                entry = Entry(ref, value, page_no, line=listing.line, x0=listing.x0)
                if (
                    not entry.not_populated
                    and (is_ignored(value) or len(value) > 50)
                    # 'Q1-Q6  See Text' (GuitarPCB G-02 Animal) is read, to be warned.
                    and not POINTER_VALUE.fullmatch(value)
                ):
                    continue
                if ref in constants.KEYWORDS and not entry.not_populated:
                    # Named controls must carry a real value (B100K, SPDT ...).
                    has_digit = any(ch.isdigit() for ch in value)
                    if not has_digit and not constants.SWITCH_VALUE_PATTERN.search(
                        value.upper()
                    ):
                        continue
                if (
                    _NUMBERED_NAME.fullmatch(ref)
                    and not is_designator(ref)
                    and any(ch.isdigit() for ch in value)
                    and any(ch.isalpha() for ch in value)  # not a bare quantity
                    and categorize_part(ref, value) is None
                ):
                    # Trumpeter's 'IQ1  2N5088' (a typo for Q1) is no known part,
                    # but is plainly listed as one.
                    check_unclassified_row(stats, ref, value)
                    continue
                entries.append(entry)
        entries += _counted_entries(page_no, data.lines, stats)
    return entries


# Rows this close (points) at their left edge share a column.
_COUNTED_COLUMN_PT = 12.0
# A counted value naming its designator: '150k (Ra)'.
_NAMED_COUNT = re.compile(r"(?P<value>.+?)\s*\((?P<ref>[A-Za-z]{1,3}\d*[a-z]?)\)")
# The part's kind restated after its value: '2.2k resistor', '4k7 (4.7k resistor)'.
_RESTATED_KIND = re.compile(r"\s*\bresistors?\b", re.I)
# A decimal comma: '0,01µF' (Tonepad Wah). Only after a zero: '1,000µF' is
# a thousand.
_DECIMAL_COMMA = re.compile(r"(?<![\d,.])0,(?=\d)")
# 'Meg' for mega: '1Meg' (Tonepad Wah).
_MEG = re.compile(r"(?<=\d)Meg\b", re.I)
# A trimmer's taper means nothing to buy: '10k lin. trim' is a 10k trimmer.
_TRIM_TAPER = re.compile(r"\s*\b(?:lin|log)\b\.?(?=.*\btrim)", re.I)


def _counted_value(printed: str) -> str:
    """A counted row's value as a part to buy, without restatements."""
    value = _RESTATED_KIND.sub("", clean_value(printed))
    value = _MEG.sub("M", _DECIMAL_COMMA.sub("0.", value))
    return " ".join(_TRIM_TAPER.sub("", value).split())


def _counted_entries(
    page_no: int, lines: list[list[Cell]], stats: ReadingStats
) -> list[Entry]:
    """Reads the rows of a page that count parts by value ('4 - 10k', Tonepad).

    A row whose category neither its heading nor its value states is not
    read. Where its column has rows that were read, it is a part of the list
    ('3 - jumpers' under Other), and is kept in `stats.unread_counted` for
    `check_quantity_list`; elsewhere it is a switch position ('1 - Off Axis'
    under 'S1 = MIC', Tonepad SansAmp).
    """
    rows = counted_parts(lines)
    values = [_counted_value(row.value) for row in rows]
    # Classified as printed: '2k2 (2.2k resistor)' under 'Millennium' says
    # what it is (Tonepad DOD 280 Comp).
    categories = [
        counted_category(row.heading, clean_value(row.value)) if value else None
        for row, value in zip(rows, values, strict=True)
    ]
    read_columns = {
        round(row.x0) for row, cat in zip(rows, categories, strict=True) if cat
    }
    entries = []
    for row, value, category in zip(rows, values, categories, strict=True):
        if category is None:
            if any(abs(row.x0 - x) <= _COUNTED_COLUMN_PT for x in read_columns):
                stats.unread_counted.append(row.text)
            continue
        named = _NAMED_COUNT.fullmatch(value)
        if row.count == 1 and named and is_designator(named.group("ref")):
            # '1 - 150k (Ra)*' (Tonepad Noise Gate) names its designator.
            entries.append(
                Entry(
                    named.group("ref"),
                    named.group("value"),
                    page_no,
                    line=row.line,
                    x0=row.x0,
                    hint=category,
                )
            )
            continue
        entries.append(
            Entry(
                f"#{row.text}",
                value,
                page_no,
                line=row.line,
                x0=row.x0,
                hint=category,
                qty=row.count,
                counted=True,
            )
        )
    return entries


def _cell_value(cells: list[Cell], listing: Listing) -> bool:
    """Whether a listing's value is just the cell after its designator.

    On a ruled table's line, a value that runs on over further cells has
    absorbed the next column ('C1 | 22n | P1-Reso | B50k Lin', GuitarPCB
    Swah). Only a voltage rating may follow ('C9 | 1n | 63V').
    """
    k = next((i for i, c in enumerate(cells) if c.x0 == listing.x0), None)
    if k is None:
        return False
    after = [c.text for c in cells[k + 1 : k + 3]]
    if not after or not clean_value(after[0]):
        return False  # 'C8A | * | 63V' (MoWah): the value is in the notes
    readings = [after[:1]]
    if len(after) == 2 and RATING_CELL.fullmatch(after[1]):
        readings.append(after)
    if len(after) == 2 and names_control(after[0]):
        readings.append(after[1:])  # 'P1 | VOL 1 | 100k Log' (XX Double Shot)
    rest = [c.text for c in cells[k + 1 :]]
    if not any(is_designator(t.split()[0]) or names_control(t) for t in rest):
        readings.append(rest)  # 'SW 1 | 3PDT | Footswitch' ends the line (EA Tremelo)
    return any(clean_value(" ".join(r)) == listing.value for r in readings if r)
