"""Checks a document's reading against the document itself.

The readers decide what each row is; these checks look at the pages and the
finished builds from other angles and warn wherever the two disagree (see
`verification.py`). Nothing here changes what was read, only what the
builder is told to check.
"""

from collections import Counter
from dataclasses import dataclass

from star_ground.bom_lib.builds import Entry
from star_ground.bom_lib.enums import ParseWarningKind
from star_ground.bom_lib.pdf.bom_table import bom_valueless_refs
from star_ground.bom_lib.pdf.page_checks import (
    pictured_lists,
    unreadable_rows,
    valueless_refs,
)
from star_ground.bom_lib.pdf.rows import REPEAT, is_not_populated
from star_ground.bom_lib.pdf.scan import find_listings
from star_ground.bom_lib.pdf.text_tables import without_note_cells
from star_ground.bom_lib.types import (
    BuildVariant,
    Inventory,
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


@dataclass(frozen=True, slots=True)
class ReadingFacts:
    """A finished reading, with the pages it was read from.

    Attributes:
        pages: Every page's tables, text and words, in document order.
        entries: Every row the readers read.
        bom_pages: The pages holding a text bill of materials, or None.
        union: Every part of every build and option.
        variants: The builds, when the document has several.
        tables_only: Whether the pages hold only their parts tables (OCR).
    """

    pages: list[PDFPageExtraction]
    entries: list[Entry]
    bom_pages: set[int] | None
    union: Inventory
    variants: tuple[BuildVariant, ...]
    tables_only: bool = False

    @property
    def texts(self) -> list[str]:
        """Each page's text."""
        return [page.text for page in self.pages]

    def on_parts_list(self, page: int) -> bool:
        """Whether a page is one the readers read: any page, without a text BOM."""
        return self.bom_pages is None or page in self.bom_pages


def check_reading(facts: ReadingFacts, stats: ReadingStats) -> None:
    """Runs every document-level self-check, warning on `stats`.

    Every designator listed in the document must be accounted for, in some
    build or option, and every value must agree with what the document says
    elsewhere.

    Args:
        facts: The reading and its pages.
        stats: The reading's statistics, which receive the warnings.
    """
    union, page_texts = facts.union, facts.texts
    check_parts_found(
        stats, sum(len(d.refs) + sum(d.counted.values()) for d in union.values())
    )
    check_value_keyed_list(page_texts, stats)
    counted = [e.ref.removeprefix("#") for e in facts.entries if e.counted]
    check_quantity_list(page_texts, counted, stats.unread_counted, stats)
    omitted = frozenset(
        r
        for e in facts.entries
        if is_not_populated(e.value)
        for r in expand_refs(e.ref)
    )
    check_unextracted_refs(_listed_texts(facts), union, stats, omitted)
    check_valueless_refs(
        _valueless_refs(facts), union if not facts.variants else Inventory(), stats
    )
    if facts.tables_only:
        check_unreadable_rows(
            [row for data in facts.pages for row in unreadable_rows(data.words)],
            stats,
        )
    check_footnotes(page_texts, stats)
    check_numbered_footnotes(
        [data.marked for data in facts.pages], page_texts, union, stats
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
    check_pictured_lists(_pictured(facts), _refs_by_page(facts), stats)
    _check_other_lists(facts, stats)
    check_bom_reconciliation(page_texts, union, stats)
    if counted or any(w.kind == ParseWarningKind.QUANTITY_LIST for w in stats.warnings):
        # A list that counts parts ('3 - 1N914') names no designators for them,
        # so the schematic's D1-D3 are 'missing' by design: the parts were read
        # by count, or the document is told they were not read.
        stats.warnings[:] = [
            w for w in stats.warnings if w.kind != ParseWarningKind.UNEXTRACTED_REF
        ]


def _listed_texts(facts: ReadingFacts) -> list[str]:
    """The text of each page where parts are listed; empty for the others.

    Like the readers, a text bill of materials bounds where parts are listed:
    Q-Tune's build steps mention 'the X1 connector area'.
    """
    return [
        without_note_cells(data, data.text) if facts.on_parts_list(k) else ""
        for k, data in enumerate(facts.pages)
    ]


def _valueless_refs(facts: ReadingFacts) -> list[str]:
    """Designators printed with no value on the parts list's pages.

    A build's list on its own page (Aion FX legacy Cerulean: 'DX1-2  ²' in the
    stock list, '1S1588' in King of Tone's) leaves a part empty although
    another build fills it, so a designator read on that page is no gap.
    """
    read_on: dict[int, set[str]] = {}
    for e in facts.entries:
        read_on.setdefault(e.page, set()).update(
            r.upper() for r in expand_refs(REPEAT.sub("", e.ref))
        )
    return [
        ref
        for k, data in enumerate(facts.pages)
        if facts.on_parts_list(k)
        for ref in [*valueless_refs(data.words), *bom_valueless_refs(data.words)]
        if not facts.variants or ref.upper() not in read_on.get(k, set())
    ]


def _pictured(facts: ReadingFacts) -> dict[int, list[str]]:
    """The designators of parts lists drawn as pictures, by page."""
    return {
        k: found
        for k, data in enumerate(facts.pages)
        if (found := pictured_lists(data.words, data.pictures))
    }


def _refs_by_page(facts: ReadingFacts) -> dict[int, set[str]]:
    """The designators read on each page."""
    refs: dict[int, set[str]] = {}
    for e in facts.entries:
        refs.setdefault(e.page, set()).add(REPEAT.sub("", e.ref))
    return refs


def _check_other_lists(facts: ReadingFacts, stats: ReadingStats) -> None:
    """Warns of another parts list on a page the readers kept away from.

    Only a document with a text bill of materials keeps its readers to some
    pages; a table of listings elsewhere may be a second board's list.
    """
    if facts.bom_pages is None:
        return
    read: dict[str, str] = {}
    for e in facts.entries:
        read.setdefault(e.ref, e.value)
    for k, data in enumerate(facts.pages):
        # A schematic's 'REF value' labels are a drawing (Aion FX Procyon).
        if k in facts.bom_pages or is_schematic_page(data.text):
            continue
        listings = find_listings(data.words)
        columns = Counter(round(listing.x0) for listing in listings)
        # A table, not labels scattered over a schematic or board render.
        if len(listings) >= _OTHER_LIST_MIN and max(columns.values()) >= (
            _OTHER_LIST_ALIGNED
        ):
            check_other_list(k, [(li.ref, li.value) for li in listings], read, stats)
