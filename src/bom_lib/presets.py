"""
src/bom_lib/presets.py
Logic for querying and managing BOM presets.
"""

import re
from collections import defaultdict
from typing import Any

try:
    from ._presets_data import BOM_PRESETS
except ImportError:
    BOM_PRESETS = {}


def get_preset_metadata() -> tuple[
    list[str], dict[str, list[str]], list[dict[str, Any]]
]:
    """
    Parses BOM_PRESETS keys into a queryable structure.
    Returns:
        sources (list): Unique sources (e.g., 'PedalPCB', 'Tayda')
        categories (dict): Map of Source -> List of Categories
        lookup (list): List of dicts {'key', 'source', 'category', 'name'}
    """
    lookup = []
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

    return (
        sorted(list(sources)),
        {k: sorted(list(v)) for k, v in categories.items()},
        lookup,
    )
