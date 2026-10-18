"""Build planning: which extracted entries belong to which build of a document.

Most documents describe one circuit. Some describe several (*variants*: Muffin
Fuzz's eight versions, a Germanium and a Silicon parts list, a "Cleft mod"),
and some offer interchangeable choices for one spot (*options*: through-hole or
SMD transistors, a rotary or a toggle switch). This module looks at where each
extracted entry sits on the page and splits the entries into:

- ``common``: parts every build needs;
- ``variants``: the parts that differ per circuit (the builder must choose);
- ``options``: interchangeable choices (the first is used by default).

Each detector recognizes one layout found in PedalPCB documents. A document
whose structure is not recognized keeps a single build, and its conflicting
duplicates are still flagged by the parser's self-checks, so an unrecognized
layout is never silently merged into one build.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from itertools import pairwise

from src.bom_lib import constants
from src.bom_lib.enums import ComponentCategory
from src.bom_lib.layout import Cell
from src.bom_lib.types import PDFWord

# Entries this close to a column's left edge (points) belong to that column.
_COLUMN_TOLERANCE_PT = 25.0
# A table counts as another variant's parts list when it shares this fraction
# of its designators with another table on a different page.
_VARIANT_TABLE_OVERLAP = 0.6
_MIN_VARIANT_TABLE_REFS = 8
# Repeated parts lists (see `_repeat_variants`): the share of labels that must
# repeat the same number of times.
_REPEAT_SHARE = 0.7
_REPEAT_SUFFIX = re.compile(r" \(\d+\)$")

_MOD_HEADING = re.compile(
    r"[^A-Za-z]*(?P<name>[A-Za-z][A-Za-z ]*\bmod(?:ification)?)\W*", re.I
)
# 'To build the vintage Octavia, with no tone pot' (Five Cats): the parts
# below build that version.
_BUILD_HEADING = re.compile(
    r"to build (?:the |an? )?(?P<name>[A-Za-z][A-Za-z0-9' -]*?)\s*(?:[,(:]|$)", re.I
)
_CHOOSE_BETWEEN = re.compile(r"choose between (?P<a>.+?) or (?P<b>.+?)\W*$", re.I)
# 'designed for either Nature control, or Treble + Bass controls' (Fuchsia).
_EITHER_OR = re.compile(
    r"\beither\s+(?:the\s+)?(?P<a>.+?)\s+controls?,?\s+or\s+(?:the\s+)?(?P<b>.+?)\s+controls?\b",
    re.I,
)
_NAME_LIST = re.compile(r"\s*(?:\+|,|&|\band\b)\s*", re.I)
_SUBTITLE = re.compile(r"^\((?P<name>[^()]+)\)$|^(?P<plain>.*\bversion\b.*)$", re.I)


@dataclass(frozen=True, slots=True)
class Entry:
    """One designator/value pair extracted from a document.

    Attributes:
        ref: The designator.
        value: The value as extracted (possibly enriched from a TYPE column).
        page: Zero-based page index.
        line: Visual line index on the page (layout entries); -1 for table rows.
        x0: Left edge of the designator, in points (layout entries).
        table: Index of the source table (table rows); None for layout entries.
        hint: Category suggested by a TYPE column.
        follows_or: Whether an 'OR' row separates this table row from the
            row above it.
        qty: How many of each designator to buy ('TERMINAL BLOCK (Qty 3)').
        column: The version column of a value-column grid the value sits in
            (MUFF'N's 'Ram', 'Triangle', ...); None for ordinary rows.
    """

    ref: str
    value: str
    page: int
    line: int = -1
    x0: float = 0.0
    table: int | None = None
    hint: ComponentCategory | None = None
    follows_or: bool = False
    qty: int = 1
    column: str | None = None

    @property
    def not_populated(self) -> bool:
        """Whether the document marks this designator as deliberately empty."""
        return self.value.upper() in constants.NOT_POPULATED_VALUES or bool(
            constants.NOT_POPULATED_REMARK.match(self.value)
        )


@dataclass(frozen=True, slots=True)
class PageContext:
    """Layout information the detectors need for one page.

    Attributes:
        lines: The cells of each visual line (see `layout.page_lines`).
        text: The page's extracted text.
        labels: Margin labels beside the page's tables (see
            `PDFPageExtraction`).
    """

    lines: list[list[Cell]]
    text: str
    labels: tuple[PDFWord, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedVariant:
    """The entries specific to one circuit of a multi-circuit document."""

    name: str
    entries: tuple[Entry, ...]


@dataclass(frozen=True, slots=True)
class PlannedOption:
    """A group of interchangeable choices; the first is the document's default."""

    group: str
    choices: tuple[tuple[str, tuple[Entry, ...]], ...]


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """How a document's entries combine into builds.

    Attributes:
        common: Entries every build needs.
        variants: Per-circuit entries; empty for single-circuit documents.
        options: Interchangeable choice groups.
    """

    common: tuple[Entry, ...]
    variants: tuple[PlannedVariant, ...] = ()
    options: tuple[PlannedOption, ...] = ()

    def build_entries(self, variant: PlannedVariant | None = None) -> list[Entry]:
        """Entries for one build: common parts, the variant's, and default options."""
        entries = list(self.common)
        if variant is not None:
            entries += variant.entries
        for option in self.options:
            entries += option.choices[0][1]
        return entries


def plan_builds(entries: list[Entry], pages: list[PageContext]) -> BuildPlan:
    """Splits extracted entries into common parts, variants, and options.

    Args:
        entries: Every entry, in extraction order.
        pages: Layout context for each page.

    Returns:
        The build plan. A single-circuit document has no variants.
    """
    variants = (
        _column_variants(entries)
        # Before grids: a mod table beside the parts list shares its lines.
        or _mod_variants(entries, pages)
        or _grid_variants(entries, pages)
        or _table_variants(entries, pages)
        or _repeat_variants(entries, pages)
        or _either_or_variants(entries, pages)
    )
    in_variants = {id(e) for v in variants for e in v.entries}
    variant_refs = {e.ref for v in variants for e in v.entries}
    # Drop common entries a variant redefines ('IC1  ** SEE BELOW **').
    # A repeated label is the same designator ('R1 (2)', `_repeat_variants`).
    common = [
        e
        for e in entries
        if id(e) not in in_variants
        and _REPEAT_SUFFIX.sub("", e.ref) not in variant_refs
    ]

    options = (
        _or_options(common, pages)
        + _table_or_options(common)
        + _choose_between_options(common, pages)
    )
    in_options = {id(e) for o in options for _, c in o.choices for e in c}
    common = [e for e in common if id(e) not in in_options]
    return BuildPlan(tuple(common), tuple(variants), tuple(options))


# --------------------------------------------------------------------- grids


def _grid_variants(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedVariant]:
    """Side-by-side parts lists: the same designator repeated across one line.

    Muffin Fuzz prints three versions per page, FX5X its OD+/Classic Tube/Supra
    table: each line repeats a designator once per version, and the line above
    the first such row names the versions.
    """
    found: dict[str, list[Entry]] = {}
    for page_no, page in enumerate(pages):
        by_line: dict[int, list[Entry]] = {}
        for e in entries:
            if e.page == page_no and e.line >= 0:
                by_line.setdefault(e.line, []).append(e)
        line = 0
        while line < len(page.lines):
            row = by_line.get(line, [])
            counts = Counter(e.ref for e in row)
            ref, repeats = counts.most_common(1)[0] if counts else ("", 0)
            if repeats < 2:
                line += 1
                continue
            starts = sorted(e.x0 for e in row if e.ref == ref)
            names = _column_names(
                page.lines[line - 1] if line else [], starts, by_line, line - 1
            )
            # No header: name each column by its first listing ('MID C10K Dual'
            # / 'MID C100K Dual', Frost Drive's alternate pot).
            first = sorted((e for e in row if e.ref == ref), key=lambda e: e.x0)
            names = [
                f"{e.ref} {e.value}" if name == f"Variant {k + 1}" else name
                for k, (name, e) in enumerate(zip(names, first, strict=True))
            ]
            groups: list[list[Entry]] = [[] for _ in starts]
            while by_line.get(line):
                for e in by_line[line]:
                    groups[_column_of(e.x0, starts)].append(e)
                line += 1
            for name, group in zip(names, groups, strict=True):
                found.setdefault(name, []).extend(group)
    return [PlannedVariant(name, tuple(group)) for name, group in found.items()]


def _column_variants(entries: list[Entry]) -> list[PlannedVariant]:
    """One variant per value column of a value-column grid (MUFF'N, RATT Deluxe)."""
    columns: dict[str, list[Entry]] = {}
    names: dict[str, str] = {}
    for e in entries:
        if e.column is not None:
            # RATT Deluxe's tables say 'GPCB Values' and 'GPCB Value'.
            key = re.sub(r"s\b", "", e.column.lower())
            names.setdefault(key, e.column)
            columns.setdefault(key, []).append(e)
    if len(columns) < 2:
        return []
    return [PlannedVariant(names[key], tuple(group)) for key, group in columns.items()]


def _column_of(x0: float, starts: list[float]) -> int:
    return max(
        (k for k, start in enumerate(starts) if x0 >= start - _COLUMN_TOLERANCE_PT),
        default=0,
    )


def _column_names(
    header: list[Cell],
    starts: list[float],
    by_line: dict[int, list[Entry]],
    header_line: int,
) -> list[str]:
    """Names for each grid column, from the header line above the grid."""
    names = [f"Variant {k + 1}" for k in range(len(starts))]
    if header_line < 0 or by_line.get(header_line):
        return names
    for k in range(len(starts)):
        text = " ".join(c.text for c in header if _column_of(c.x0, starts) == k)
        # The end of a sentence is not a name ('... scaled accordingly.').
        if text and not text.endswith(".") and not text[0].islower():
            names[k] = text
    return names


# ---------------------------------------------------------- repeated lists


def _repeat_variants(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedVariant]:
    """Whole parts lists printed one after another, one per build.

    Five Cats Rat King lists every part four times, once per RAT version, in
    typed tables that number a repeated label ('R1 (2)'). When nearly every
    label repeats the same number of times, each repeat is a build, named by
    the margin label beside its rows ('Standard RAT', 'Dirty RAT'), which
    every build must have.
    """
    occurrences: dict[str, list[Entry]] = {}
    for e in entries:
        base = _REPEAT_SUFFIX.sub("", e.ref).upper()
        occurrences.setdefault(base, []).append(e)
    counts = Counter(len(es) for es in occurrences.values())
    builds, labels = counts.most_common(1)[0] if counts else (0, 0)
    if (
        builds < 2
        or labels < _MIN_VARIANT_TABLE_REFS
        or labels < _REPEAT_SHARE * len(occurrences)
    ):
        return []
    # Each build spans the rows its labels' k-th listings cover; a label OCR
    # misread ('C33' for 'C3') falls in the span it was printed in.
    complete = [es for es in occurrences.values() if len(es) == builds]
    spans = [
        (
            min(_position(es[k]) for es in complete),
            max(_position(es[k]) for es in complete),
        )
        for k in range(builds)
    ]
    groups: list[list[Entry]] = [[] for _ in range(builds)]
    for e in entries:
        at = _position(e)
        k = next((k for k, (lo, hi) in enumerate(spans) if lo <= at <= hi), None)
        if k is not None:
            groups[k].append(replace(e, ref=_REPEAT_SUFFIX.sub("", e.ref)))
    # Only named builds: unnamed repeats are as likely a second board (GuitarPCB
    # Fuzz God) or a schematic beside the list (Aion FX legacy).
    names = [_margin_label(group, pages) for group in groups]
    if None in names or len(set(names)) < len(names):
        return []
    return [
        PlannedVariant(str(name), tuple(group))
        for name, group in zip(names, groups, strict=True)
    ]


def _position(e: Entry) -> tuple[int, int]:
    return (e.page, e.line)


def _margin_label(group: list[Entry], pages: list[PageContext]) -> str | None:
    """The margin label beside a build's rows, if exactly one sits there."""
    if not group:
        return None
    page_no = Counter(e.page for e in group).most_common(1)[0][0]
    page = pages[page_no]
    rows = [e for e in group if e.page == page_no and 0 <= e.line < len(page.lines)]
    if not rows or not page.labels:
        return None
    tops = [page.lines[e.line][0].words[0]["top"] for e in rows]
    lo, hi = min(tops), max(tops)
    lines: dict[float, list[str]] = {}
    for w in sorted(page.labels, key=lambda w: w["x0"]):
        if lo <= w["top"] <= hi:
            lines.setdefault(w["top"], []).append(w["text"])
    return " ".join(next(iter(lines.values()))) if len(lines) == 1 else None


# ----------------------------------------------------------- variant tables


def _table_variants(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedVariant]:
    """One complete parts table per variant, on separate pages (Antithesis)."""
    tables: dict[int, list[Entry]] = {}
    for e in entries:
        if e.table is not None:
            tables.setdefault(e.table, []).append(e)
    candidates = [t for t in tables.values() if len(t) >= _MIN_VARIANT_TABLE_REFS]
    if len(candidates) < 2:
        return []
    if len({t[0].page for t in candidates}) < len(candidates):
        # Tables side by side on one page are two boards of one build
        # (GuitarPCB Tweed Fuzztone), not alternative versions.
        return []
    ref_sets = [{e.ref for e in t} for t in candidates]
    for a, b in pairwise(ref_sets):
        if len(a & b) < _VARIANT_TABLE_OVERLAP * min(len(a), len(b)):
            return []
    variants = []
    for k, table in enumerate(candidates):
        name = _page_subtitle(pages[table[0].page].text) or f"Version {k + 1}"
        variants.append(PlannedVariant(name, tuple(table)))
    return variants


def _page_subtitle(text: str) -> str | None:
    """The line under a page title, when it names a version ('(Germanium Version)')."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[1:3]:
        m = _SUBTITLE.match(line)
        if m:
            return (m.group("name") or m.group("plain")).strip()
    return None


# ------------------------------------------------------- modification blocks


def _mod_variants(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedVariant]:
    """A '... Mod' heading followed by re-listed designators in its column.

    A 'To build the ...' heading names a version the same way; its table can
    also add parts the main list lacks ('Volume | A1M', Octavia), and the
    heading can be split into a cell per word (OCR).
    """
    for page_no, page in enumerate(pages):
        for line_no, cells in enumerate(page.lines):
            for k, cell in enumerate(cells):
                m = _MOD_HEADING.fullmatch(cell.text)
                if m and len(cell.text.split()) <= 4:
                    name, additions = m.group("name"), False
                else:
                    b = _BUILD_HEADING.match(" ".join(c.text for c in cells[k:]))
                    if b is None:
                        continue
                    name, additions = b.group("name"), True
                block = _column_entries_below(entries, page_no, line_no, cell.x0)
                outside = {e.ref for e in entries if e not in block}
                overrides = [e for e in block if e.ref in outside]
                if not overrides:
                    continue
                overridden = {e.ref for e in overrides}
                stock = tuple(
                    e for e in entries if e.ref in overridden and e not in block
                )
                mod = block if additions else overrides
                return [
                    PlannedVariant("Stock", stock),
                    PlannedVariant(" ".join(name.split()).title(), tuple(mod)),
                ]
    return []


def _either_or_variants(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedVariant]:
    """A note offering alternative controls: 'either Nature control, or Treble + Bass controls'.

    Each side names controls listed in the parts list; a builder installs one
    side or the other. The note is trusted only when every name it gives is a
    listed designator, and no name appears on both sides.
    """
    by_ref: dict[str, list[Entry]] = {}
    for e in entries:
        by_ref.setdefault(e.ref.upper(), []).append(e)
    for page in pages:
        text = " ".join(page.text.split())
        for m in _EITHER_OR.finditer(text):
            sides = [m.group("a"), m.group("b")]
            names = [[n for n in _NAME_LIST.split(side) if n] for side in sides]
            refs = [[n.upper() for n in side] for side in names]
            flat = [r for side in refs for r in side]
            if len(set(flat)) != len(flat) or not all(r in by_ref for r in flat):
                continue
            return [
                PlannedVariant(
                    " + ".join(n.title() for n in side_names),
                    tuple(e for r in side_refs for e in by_ref[r]),
                )
                for side_names, side_refs in zip(names, refs, strict=True)
            ]
    return []


def _column_entries_below(
    entries: list[Entry], page: int, line: int, x0: float
) -> list[Entry]:
    return [
        e
        for e in entries
        if e.page == page and e.line > line and abs(e.x0 - x0) <= _COLUMN_TOLERANCE_PT
    ]


# ------------------------------------------------------------------ options


def _or_options(entries: list[Entry], pages: list[PageContext]) -> list[PlannedOption]:
    """'SW1 2P4T Mini Rotary' / 'OR' / 'SW1 SPDT ON/OFF/ON' (Parentheses)."""
    options = []
    for page_no, page in enumerate(pages):
        for line_no, cells in enumerate(page.lines):
            for cell in cells:
                if cell.text.strip().upper() != "OR":
                    continue
                near = [
                    e
                    for e in entries
                    if e.page == page_no and abs(e.x0 - cell.x0) <= _COLUMN_TOLERANCE_PT
                ]
                above = [e for e in near if e.line < line_no]
                below = [e for e in near if e.line > line_no]
                if not above or not below:
                    continue
                first = max(above, key=lambda e: e.line)
                second = min(below, key=lambda e: e.line)
                if first.ref == second.ref:
                    options.append(
                        PlannedOption(
                            first.ref,
                            ((first.value, (first,)), (second.value, (second,))),
                        )
                    )
    return options


def _table_or_options(entries: list[Entry]) -> list[PlannedOption]:
    """An 'OR' row inside a ruled table between alternatives for one position.

    Shindig: 'D10  DI1510  Full-wave bridge rectifier' / 'OR' / 'D10a'-'D10d'
    1N4007. The rows on each side of the OR that share the designator's base
    (D10, D10a) are the two choices; when the two sides name different parts
    (Spelunker's GAIN_TRIM or GAIN), the single rows beside the OR are.
    """
    options = []
    for k, entry in enumerate(entries):
        if not entry.follows_or or k == 0:
            continue
        base = _base_ref(entry.ref)
        same_table = [e for e in entries if e.table == entry.table]
        at = same_table.index(entry)
        before = _run_with_base(reversed(same_table[:at]), base)[::-1]
        after = _run_with_base(same_table[at:], base)
        if not before and at > 0:
            # Spelunker: 'GAIN_TRIM' / 'Choose between ... or ...' / 'GAIN'
            # names two different parts; the rows beside the note are the choices.
            before, after = [same_table[at - 1]], [entry]
        if before and after:
            options.append(
                PlannedOption(
                    base,
                    (
                        (before[0].value, tuple(before)),
                        (after[0].value, tuple(after)),
                    ),
                )
            )
    return options


def _base_ref(ref: str) -> str:
    """'D10a' -> 'D10': a designator without its channel-letter suffix."""
    return re.sub(r"(?<=\d)[A-Ha-h]$", "", ref)


def _run_with_base(entries: Iterable[Entry], base: str) -> list[Entry]:
    run = []
    for e in entries:
        if _base_ref(e.ref) != base:
            break
        run.append(e)
    return run


def _choose_between_options(
    entries: list[Entry], pages: list[PageContext]
) -> list[PlannedOption]:
    """'Choose between Through-Hole or SMD', then a sub-heading per choice (Mesmerizer)."""
    options = []
    for page_no, page in enumerate(pages):
        for line_no, cells in enumerate(page.lines):
            for cell in cells:
                m = _CHOOSE_BETWEEN.search(cell.text)
                if not m:
                    continue
                names = [m.group("a").strip(), m.group("b").strip()]
                choices = _choices_under_subheadings(
                    entries, page_no, page, line_no, cell.x0, names
                )
                if len(choices) == len(names) and all(c for _, c in choices):
                    group = _heading_above(page, line_no, cell.x0) or " or ".join(names)
                    options.append(PlannedOption(group, tuple(choices)))
    return options


def _choices_under_subheadings(
    entries: list[Entry],
    page_no: int,
    page: PageContext,
    after_line: int,
    x0: float,
    names: list[str],
) -> list[tuple[str, tuple[Entry, ...]]]:
    wanted = {n.lower(): n for n in names}
    starts: list[tuple[int, str]] = []
    for line_no in range(after_line + 1, len(page.lines)):
        for cell in page.lines[line_no]:
            if (
                abs(cell.x0 - x0) <= _COLUMN_TOLERANCE_PT
                and cell.text.strip().lower() in wanted
            ):
                starts.append((line_no, wanted[cell.text.strip().lower()]))
    choices = []
    for k, (line_no, name) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(page.lines)
        block = tuple(
            e
            for e in entries
            if e.page == page_no
            and line_no < e.line < end
            and abs(e.x0 - x0) <= _COLUMN_TOLERANCE_PT
        )
        # The block ends at the first gap: a later section in the same column
        # (DIODES under the SMD transistors) is not part of the choice.
        contiguous: list[Entry] = []
        for e in sorted(block, key=lambda e: e.line):
            if contiguous and e.line > contiguous[-1].line + 1:
                break
            contiguous.append(e)
        choices.append((name, tuple(contiguous)))
    return choices


def _heading_above(page: PageContext, line: int, x0: float) -> str | None:
    """The nearest column heading above a line (e.g. 'TRANSISTORS' -> 'Transistors')."""
    for line_no in range(line - 1, -1, -1):
        for cell in page.lines[line_no]:
            text = cell.text.strip()
            if (
                abs(cell.x0 - x0) <= _COLUMN_TOLERANCE_PT
                and text.isupper()
                and text.isalpha()
            ):
                return text.title()
    return None
