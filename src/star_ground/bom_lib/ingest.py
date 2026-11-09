"""Turns one parts-list row into inventory parts, whatever format it came from."""

import re

from star_ground.bom_lib import constants
from star_ground.bom_lib.classifier import (
    categorize_part,
)
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.enums import ComponentCategory
from star_ground.bom_lib.types import (
    Inventory,
    ReadingStats,
    make_component_key,
)
from star_ground.bom_lib.utils import expand_refs
from star_ground.bom_lib.verification import (
    check_classification,
    check_duplicate,
    check_pointer_value,
    resolve_value_choice,
)


def ingest_bom_line(
    inventory: Inventory,
    source: str,
    ref_raw: str,
    val_raw: str,
    stats: ReadingStats | None = None,
    category_hint: ComponentCategory | None = None,
    qty: int = 1,
    inject: bool = True,
) -> int:
    """Core ingestion kernel: Expands refs, classifies, and updates inventory.

    This function bridges the gap between raw text (R1-R4, 10k) and the
    structured inventory. It handles:
    1. Reference expansion (R1-R4 -> R1, R2, R3, R4).
    2. De-duplication (checking if R1 was already seen in this session).
    3. Categorization (identifying parts).
    4. Auto-injection (adding sockets for ICs).

    Args:
        inventory: The master inventory dictionary to update.
        source: The name of the file/project being ingested.
        ref_raw: Raw reference string (e.g., "R1-R4").
        val_raw: Raw value string (e.g., "10k").
        stats: Optional stats object to track duplicate refs and counts.
        category_hint: Category from a TYPE column, for named parts the
            designator and value cannot classify.
        qty: How many of each designator to buy ('TERMINAL BLOCK (Qty 3)').
        inject: Whether to add implied parts (IC sockets); off when the
            document lists its own sockets (Aion FX: 'IC1-S | DIP-8 socket').

    Returns:
        The number of valid parts successfully found and recorded.
    """
    parts_found = 0
    expanded_refs = expand_refs(ref_raw)
    val_raw = resolve_value_choice(stats, ref_raw, val_raw)

    pointer = _POINTER_TAIL.search(val_raw)
    if pointer and val_raw[: pointer.start()].strip():
        # 'PNP—See Text' (GuitarPCB PUFF v4): a kind of part, chosen in the notes.
        if stats is not None:
            for r in expanded_refs:
                check_pointer_value(stats, r, val_raw)
        val_raw = val_raw[: pointer.start()].strip()
    for r in expanded_refs:
        if POINTER_VALUE.fullmatch(val_raw.strip()):
            # 'Q1-Q6  See Text' (GuitarPCB G-02 Animal): the value is in the
            # build notes, so there is nothing to buy until the builder reads it.
            if stats is not None:
                check_pointer_value(stats, r, val_raw)
            continue
        result = categorize_part(r, val_raw, category_hint)
        if result is None:
            # Not a part (e.g. a jumper pad setting), so it cannot conflict
            # with anything either.
            continue

        # De-dupe Check
        if stats is not None:
            if not is_designator(r) and r not in stats.seen_refs:
                # A named label used twice is two parts, bought under 'COMP (2)'
                # as in typed tables (Aion FX legacy L5 Preamp: 'COMP  5MM LED'
                # and 'Comp  SPDT').
                taken = {ref.upper() for ref in stats.seen_refs}
                if r.upper() in taken:
                    n = 2
                    while f"{r.upper()} ({n})" in taken:
                        n += 1
                    r = f"{r} ({n})"
            if r in stats.seen_refs:
                check_duplicate(stats, r, stats.seen_refs[r], val_raw)
                continue
            stats.seen_refs[r] = val_raw

        if stats is not None:
            check_classification(stats, r, val_raw, result.category, result.clean_value)

        parts_found += 1
        main_key = make_component_key(result.category, result.clean_value)

        # 1. Record Main Part
        inventory.add_part(source, main_key, r, qty)

        # 2. Handle Auto-Injection (e.g., Sockets)
        if result.injected_key and inject:
            # Record auto-injected component key (e.g. IC socket)
            inventory.add_part(
                source, result.injected_key, f"{r}{constants.INJECTED_SUFFIX}"
            )

    return parts_found


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
