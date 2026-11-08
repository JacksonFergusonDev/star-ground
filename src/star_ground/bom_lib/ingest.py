"""Turns one parts-list row into inventory parts, whatever format it came from."""

import re

from star_ground.bom_lib import constants
from star_ground.bom_lib.choices import choose_value
from star_ground.bom_lib.classifier import (
    categorize_part,
)
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.enums import ComponentCategory, ParseWarningKind
from star_ground.bom_lib.types import (
    Inventory,
    ParseWarning,
    ReadingStats,
    make_component_key,
)
from star_ground.bom_lib.utils import expand_refs
from star_ground.bom_lib.verification import (
    check_classification,
    check_duplicate,
    check_pointer_value,
)


def ingest_bom_line(
    inventory: Inventory,
    source: str,
    ref_raw: str,
    val_raw: str,
    stats: ReadingStats,
    *,
    category_hint: ComponentCategory | None = None,
    qty: int = 1,
    inject: bool = True,
) -> int:
    """Reads one parts-list row into inventory parts.

    Turns raw text ('R1-R4', '10k') into parts: expands the designator range,
    picks one value where the document offers a choice, classifies each part,
    skips a designator already read, and adds implied parts (IC sockets). What
    the builder should check is recorded on `stats` as it is found.

    Args:
        inventory: The inventory to add the parts to.
        source: The name of the file or project being read.
        ref_raw: Raw reference string (e.g., "R1-R4").
        val_raw: Raw value string (e.g., "10k").
        stats: The reading's statistics: designators seen so far, and warnings.
        category_hint: Category from a TYPE column, for named parts the
            designator and value cannot classify.
        qty: How many of each designator to buy ('TERMINAL BLOCK (Qty 3)').
        inject: Whether to add implied parts (IC sockets); off when the
            document lists its own sockets (Aion FX: 'IC1-S | DIP-8 socket').

    Returns:
        The number of parts recorded.
    """
    choice = choose_value(ref_raw, val_raw)
    if choice.note is not None:
        stats.warnings.append(
            ParseWarning(
                kind=ParseWarningKind.VALUE_CHOICE, ref=ref_raw, message=choice.note
            )
        )
    refs = expand_refs(ref_raw)
    value = _without_pointer(stats, refs, choice.value)

    parts_found = 0
    for ref in refs:
        if POINTER_VALUE.fullmatch(value.strip()):
            # 'Q1-Q6  See Text' (GuitarPCB G-02 Animal): the value is in the
            # build notes, so there is nothing to buy until the builder reads it.
            check_pointer_value(stats, ref, value)
            continue
        result = categorize_part(ref, value, category_hint)
        if result is None:
            # Not a part (e.g. a jumper pad setting), so it cannot conflict
            # with anything either.
            continue
        claimed = _claim(stats, ref, value)
        if claimed is None:
            continue
        check_classification(stats, claimed, value, result.category, result.clean_value)
        parts_found += 1
        key = make_component_key(result.category, result.clean_value)
        inventory.add_part(source, key, claimed, qty)
        if result.injected_key and inject:
            # An implied part, such as an IC's socket.
            inventory.add_part(
                source, result.injected_key, f"{claimed}{constants.INJECTED_SUFFIX}"
            )
    return parts_found


def _without_pointer(stats: ReadingStats, refs: list[str], value: str) -> str:
    """A value without its pointer to the notes: 'PNP—See Text' is a PNP.

    The kind of part is read (GuitarPCB PUFF v4); which one is chosen in the
    notes, so the builder is told to read them.
    """
    pointer = _POINTER_TAIL.search(value)
    if pointer is None or not value[: pointer.start()].strip():
        return value
    for ref in refs:
        check_pointer_value(stats, ref, value)
    return value[: pointer.start()].strip()


def _claim(stats: ReadingStats, ref: str, value: str) -> str | None:
    """Records a designator as read, under the label it is bought by.

    A named label used twice is two parts, bought under 'COMP (2)' as in typed
    tables (Aion FX legacy L5 Preamp: 'COMP  5MM LED' and 'Comp  SPDT'). A
    designator read before is no new part: the duplicate is checked instead.

    Returns:
        The label to buy the part under, or None for a designator read before.
    """
    if not is_designator(ref) and ref not in stats.seen_refs:
        taken = {seen.upper() for seen in stats.seen_refs}
        if ref.upper() in taken:
            n = 2
            while f"{ref.upper()} ({n})" in taken:
                n += 1
            ref = f"{ref} ({n})"
    if ref in stats.seen_refs:
        check_duplicate(stats, ref, stats.seen_refs[ref], value)
        return None
    stats.seen_refs[ref] = value
    return ref


def record_pcb(
    inventory: Inventory, source_name: str, name: str, stats: ReadingStats
) -> None:
    """Records the project's PCB ('PCB Fuzz Face') as one part to buy.

    Args:
        inventory: The inventory to add it to.
        source_name: The project it belongs to.
        name: The board's name.
        stats: The reading's statistics, whose part count it adds to.
    """
    key = make_component_key(ComponentCategory.PCB, name)
    inventory[key].qty += 1
    if "PCB" not in inventory[key].sources[source_name]:
        inventory[key].sources[source_name].append("PCB")
    stats.parts_found += 1


# A value that only points to the build notes.
POINTER_VALUE = re.compile(r"see (?:build )?(?:notes?|text)", re.IGNORECASE)
_POINTER_TAIL = re.compile(
    r"\s*[-\u2013\u2014]\s*see (?:build )?(?:notes?|text)\s*$", re.IGNORECASE
)
