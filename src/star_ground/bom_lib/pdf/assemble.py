"""Turns the readers' entries into each build's inventory, variants and options."""

import re
from dataclasses import dataclass

from star_ground.bom_lib.builds import BuildPlan, Entry
from star_ground.bom_lib.choices import choose_value
from star_ground.bom_lib.classifier import (
    categorize_part,
    is_chip_socket,
)
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.ingest import ingest_bom_line
from star_ground.bom_lib.pdf.rows import record_not_populated, same_value
from star_ground.bom_lib.types import (
    BuildVariant,
    Inventory,
    OptionNote,
    ReadingStats,
    make_component_key,
)
from star_ground.bom_lib.verification import (
    check_second_board,
)


def _ingest_counted(
    inventory: Inventory, source_name: str, entry: Entry, inject: bool
) -> int:
    """Buys a counted row's parts, with no designator: '4 - 10k' is four 10k.

    Returns:
        The number of parts recorded.
    """
    value = choose_value(entry.ref, entry.value).value
    result = categorize_part("", value, entry.hint)
    if result is None:
        return 0
    inventory.add_counted(
        source_name, make_component_key(result.category, result.clean_value), entry.qty
    )
    if result.injected_key and inject:
        inventory.add_counted(source_name, result.injected_key, entry.qty)
    return entry.qty


def _ingest_entries(
    entries: list[Entry], source_name: str
) -> tuple[Inventory, ReadingStats]:
    """Ingests one build's entries into a fresh inventory.

    Each build gets its own duplicate tracking, so two variants listing the
    same designator are not conflicts, while a conflict inside one build is.
    A designator listed again, with the same value, in another parts table of
    the same build belongs to another board (EV Mini's LFO and Audio PCBs
    both have Q1-Q4), so it is bought again rather than merged.
    """
    inventory = Inventory()
    stats = ReadingStats()
    table_of: dict[str, int] = {}
    # A document listing its chips' sockets (Aion FX's 'IC1-S | DIP-8 socket')
    # gets none added; its DC socket or tube socket is no chip's.
    lists_sockets = any(is_chip_socket(entry.value) for entry in entries)
    for entry in entries:
        if entry.counted:
            stats.parts_found += _ingest_counted(
                inventory, source_name, entry, inject=not lists_sockets
            )
            continue
        if entry.not_populated:
            record_not_populated(stats, entry.ref, entry.value)
            continue
        ref = entry.ref
        if entry.table is not None:
            first = table_of.setdefault(entry.ref, entry.table)
            seen = stats.seen_refs.get(entry.ref)
            value = choose_value(entry.ref, entry.value).value
            if first != entry.table and any(
                same_value(seen, v) for v in (entry.value, value)
            ):
                del stats.seen_refs[entry.ref]
            elif first != entry.table and seen is not None and not entry.follows_or:
                # Another table lists the same designator with another value:
                # another board (Tweed Fuzztone's Tweed and Fuzz boards both
                # have R1-R7), or another version. Both are bought, and the
                # builder is told which is which.
                ref = _next_board_ref(stats, entry.ref)
                check_second_board(stats, entry.ref, ref, seen, entry.value)
        stats.parts_found += ingest_bom_line(
            inventory,
            source_name,
            ref,
            entry.value,
            stats,
            category_hint=entry.hint,
            qty=entry.qty,
            inject=not lists_sockets,
        )
    return inventory, stats


def _next_board_ref(stats: ReadingStats, ref: str) -> str:
    """The first free 'R1 (2)', 'R1 (3)' ... for a designator on another board."""
    n = 2
    while f"{ref} ({n})" in stats.seen_refs:
        n += 1
    return f"{ref} ({n})"


def _merge_warnings(target: ReadingStats, source: ReadingStats) -> None:
    for warning in source.warnings:
        if warning not in target.warnings:
            target.warnings.append(warning)


# A part supplied with the PCB: 'IC3  24LC32A *Included' (PedalPCB Deflector),
# 'IC2  RANDOMIZER  Random LFO microcontroller  Included with PCB' (Low Tide).
# The designator is the last one before the marker: Deflector's line holds
# three columns, 'R3 1K C3 1n IC3 24LC32A *Included'.
_INCLUDED_ROW = re.compile(
    r"\b([A-Z]{1,4}\d{1,3})\s(?:(?!\b[A-Z]{1,4}\d{1,3}\s).)*?"
    r"(?i:\bincluded(?: with (?:the )?PCB)?)\s*$",
    re.MULTILINE,
)


def included_refs(page_texts: list[str]) -> frozenset[str]:
    """Designators the document says come with the PCB.

    Args:
        page_texts: Every page's text.

    Returns:
        The designators of parts supplied with the PCB.
    """
    return frozenset(
        m.group(1)
        for text in page_texts
        for m in _INCLUDED_ROW.finditer(text)
        if is_designator(m.group(1))
    )


def mark_included(inventory: Inventory, refs: frozenset[str]) -> None:
    """Counts the parts of an inventory that come with the PCB."""
    for data in inventory.values():
        data.included = sum(1 for r in data.refs if r in refs)


@dataclass(frozen=True, slots=True)
class AssembledBuilds:
    """Every build of a document, ingested.

    Attributes:
        inventory: The single build's parts; empty when the document has
            variants.
        variants: Every build of a multi-circuit document; empty otherwise.
        options: The interchangeable choices, resolved to the document's
            first.
        union: Every build's and option choice's parts together, for the
            self-checks, which must see everything the document lists.
    """

    inventory: Inventory
    variants: tuple[BuildVariant, ...]
    options: tuple[OptionNote, ...]
    union: Inventory


def assemble_builds(
    plan: BuildPlan, source_name: str, stats: ReadingStats
) -> AssembledBuilds:
    """Ingests every build in a plan.

    Args:
        plan: The document's builds and options.
        source_name: Label for the source.
        stats: The reading's counts and warnings, updated in place.

    Returns:
        Each build's parts, the option notes, and the union of them all.
    """
    options = tuple(
        OptionNote(
            group=o.group,
            chosen=o.choices[0][0],
            alternatives=tuple(name for name, _ in o.choices[1:]),
        )
        for o in plan.options
    )
    # Merge each build and option choice separately: one variant's OMIT must not
    # hide another variant's part for the same designator.
    union = Inventory()
    builds = [plan.build_entries(v) for v in plan.variants] or [plan.build_entries()]
    builds += [list(choice) for o in plan.options for _, choice in o.choices[1:]]
    for entries in builds:
        union.merge(_ingest_entries(entries, source_name)[0])

    if len(plan.variants) < 2:
        variant = plan.variants[0] if plan.variants else None
        inventory, build_stats = _ingest_entries(
            plan.build_entries(variant), source_name
        )
        _merge_warnings(stats, build_stats)
        stats.parts_found += build_stats.parts_found
        return AssembledBuilds(inventory, (), options, union)

    variants = []
    for planned in plan.variants:
        inventory, build_stats = _ingest_entries(
            plan.build_entries(planned), source_name
        )
        _merge_warnings(stats, build_stats)
        variants.append(BuildVariant(planned.name, inventory, build_stats.parts_found))
    return AssembledBuilds(Inventory(), tuple(variants), options, union)
