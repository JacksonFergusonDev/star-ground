"""Logic for querying and managing BOM presets."""

import re
from collections import defaultdict
from typing import Any, NamedTuple, TypedDict

BOM_PRESETS: dict[str, Any]

try:
    from ._presets_data import BOM_PRESETS
except ImportError:
    BOM_PRESETS = {}


class PresetLookupEntry(TypedDict):
    """A flattened preset lookup entry for filtering and search.

    Attributes:
        full_key: The full preset dictionary key (e.g. '[PedalPCB] [Boost] Triangulum Boost').
        source: Source brand / origin (e.g. 'PedalPCB').
        category: Circuit category (e.g. 'Boost', 'Overdrive', 'Misc').
        name: Circuit name (e.g. 'Triangulum Boost').
    """

    full_key: str
    source: str
    category: str
    name: str


class PresetCatalog(NamedTuple):
    """Catalog metadata extracted from BOM presets for UI filtering.

    Attributes:
        sources: Sorted list of distinct sources (e.g. ['PedalPCB', 'Tayda']).
        categories: Mapping of source brand to sorted list of categories.
        lookup: Flattened list of preset lookup entries.
    """

    sources: list[str]
    categories: dict[str, list[str]]
    lookup: list[PresetLookupEntry]


__all__ = [
    "BOM_PRESETS",
    "PresetCatalog",
    "PresetLookupEntry",
    "get_preset_metadata",
]


def get_preset_metadata() -> PresetCatalog:
    """Parses BOM_PRESETS keys into a queryable structure.

    Returns:
        PresetCatalog containing sorted sources, category mapping, and lookup entries.
    """
    lookup: list[PresetLookupEntry] = []
    sources = set()
    categories = defaultdict(set)

    # Regex to handle "[Source] [Category] Name" or "[Source] Name"
    pattern = re.compile(r"^\[(.*?)\] (?:\[(.*?)\] )?(.*)$")

    for key in BOM_PRESETS:
        match = pattern.match(key)
        if match:
            src = match.group(1)
            cat = match.group(2) or "Misc"
            name = match.group(3)

            sources.add(src)
            categories[src].add(cat)

            lookup.append(
                {
                    "full_key": key,
                    "source": src,
                    "category": cat,
                    "name": name,
                }
            )

    return PresetCatalog(
        sources=sorted(sources),
        categories={k: sorted(v) for k, v in categories.items()},
        lookup=lookup,
    )
