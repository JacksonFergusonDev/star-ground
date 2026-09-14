"""Logic for querying and managing BOM presets."""

import re
from collections import defaultdict
from typing import NamedTuple, NotRequired, TypedDict, cast


class PresetData(TypedDict):
    """Raw BOM preset data structure.

    Attributes:
        bom_text: The complete text content of the parsed BOM preset.
        source_path: The file path to the original BOM source document.
        is_pdf: True if the source document was a PDF.
    """

    bom_text: str
    source_path: NotRequired[str]
    is_pdf: NotRequired[bool]


BOM_PRESETS: dict[str, PresetData]

try:
    from . import _presets_data

    BOM_PRESETS = cast(dict[str, PresetData], _presets_data.BOM_PRESETS)
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
    "PresetData",
    "PresetLookupEntry",
    "get_preset_metadata",
    "parse_preset_key",
]

PRESET_KEY_PATTERN = re.compile(r"^\[(.*?)\] (?:\[(.*?)\] )?(.*)$")


def parse_preset_key(raw_key: str) -> tuple[str, str, str] | None:
    """Parses '[Source] [Category] Name' or '[Source] Name' into (source, category, name).

    Args:
        raw_key: The preset key string (e.g. '[PedalPCB] [Boost] Triangulum Boost').

    Returns:
        A tuple of (source, category, name), or None if the key does not match.
    """
    if not raw_key:
        return None
    match = PRESET_KEY_PATTERN.match(raw_key)
    if match:
        src = match.group(1)
        cat = match.group(2) or "Misc"
        name = match.group(3)
        return src, cat, name
    return None


def get_preset_metadata() -> PresetCatalog:
    """Parses BOM_PRESETS keys into a queryable structure.

    Returns:
        PresetCatalog containing sorted sources, category mapping, and lookup entries.
    """
    lookup: list[PresetLookupEntry] = []
    sources = set()
    categories = defaultdict(set)

    for key in BOM_PRESETS:
        parsed = parse_preset_key(key)
        if parsed:
            src, cat, name = parsed
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
