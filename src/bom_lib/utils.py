"""Utility functions for string manipulation and numeric parsing.

This module handles the low-level formatting logic, including:
- Natural sorting (R1, R2, R10).
- Range expansion (R1-R5 -> R1, R2...).
- SI unit parsing (1k5 -> 1500.0).
- Search string generation (1500.0 -> 1.5k).
"""

import re
from decimal import Decimal

from src.bom_lib.grammar import parse_si_value
from src.bom_lib.types import RefDesignator


def natural_sort_key(ref: str | RefDesignator) -> list[int | str]:
    """Generates a sort key for natural alphanumeric sorting.

    Splits strings into text and numeric chunks so that 'R10' comes
    after 'R2', rather than 'R1'.

    Args:
        ref: The reference designator string or RefDesignator instance.

    Returns:
        A list of mixed types (int/str) suitable for sort keys.
    """
    raw = str(ref)
    return [
        int(text) if text.isdigit() else text.upper()
        for text in re.split(r"(\d+)", raw)
    ]


def deduplicate_refs(refs: list[str]) -> list[str]:
    """Removes duplicates and applies natural sorting to a reference list.

    Args:
        refs: A list of reference strings (e.g., ['R1', 'R10', 'R1', 'R2']).

    Returns:
        A sorted, unique list of references (e.g., ['R1', 'R2', 'R10']).
    """
    if not refs:
        return []

    unique = list(set(refs))
    return sorted(unique, key=natural_sort_key)


def expand_refs(ref_raw: str) -> list[str]:
    """Explodes range strings into individual references.

    Handles formats like 'R1-R4' or 'C1-3'. Includes a sanity check
    to prevent exploding massive invalid ranges (limit 50).

    Args:
        ref_raw: The raw reference string (e.g., "R1-4").

    Returns:
        A list of individual references (e.g., ['R1', 'R2', 'R3', 'R4']).
        Returns the original string as a single-item list if expansion fails.
    """
    ref_raw = ref_raw.strip()
    if "-" not in ref_raw:
        return [ref_raw]

    try:
        # Match pattern: Prefix1, StartNum, optional Prefix2, EndNum
        m = re.match(r"^([a-zA-Z]+)(\d+)-([a-zA-Z]+)?(\d+)$", ref_raw)
        if m:
            prefix1 = m.group(1)
            start = int(m.group(2))
            prefix2 = m.group(3)
            end = int(m.group(4))

            # If a second prefix is provided, it must match the first prefix
            if prefix2 and prefix2.upper() != prefix1.upper():
                return [ref_raw]

            # Sanity check: Avoid accidental explosion of massive numbers
            if 0 <= (end - start) < 50:
                return [
                    str(RefDesignator(prefix=prefix1, number=i))
                    for i in range(start, end + 1)
                ]
    except Exception:
        pass

    return [ref_raw]


def parse_value_to_decimal(val_str: str) -> Decimal | None:
    """Reduces component values to their base SI unit as an exact Decimal.

    Handles standard notation ('10k', '4.7u') and BS 1852 "sandwich"
    notation ('1k5').

    Args:
        val_str: The raw value string (e.g., "4k7").

    Returns:
        The Decimal value in base units (e.g., Decimal("4700.0")), or None if parsing fails.
    """
    return parse_si_value(val_str)


def float_to_search_string(val: float | None) -> str:
    """Converts a float back to a standard engineering string (e.g., '1.5k').

    This format is optimized for searching parts suppliers (e.g., Tayda).

    Args:
        val: The float value (e.g., 1500.0).

    Returns:
        A string formatted with SI suffixes (k, M, u, n, p).
        Returns empty string if val is None.
    """
    if val is None:
        return ""

    val = float(val)  # Ensure float

    # Resistor/Large values (k, M)
    # We purposefully iterate high-to-low to find the first fit.
    for suffix, multiplier in [("M", 1e6), ("k", 1e3)]:
        if val >= multiplier:
            reduced = val / multiplier
            reduced = round(reduced, 6)  # Floating point sanity

            if reduced.is_integer():
                return f"{int(reduced)}{suffix}"
            return f"{reduced:.1f}{suffix}"

    # Capacitor/Inductor values (u, n, p)
    # Applied primarily for values < 1.0 (assuming base unit is Farads/Henries)
    if val < 1.0:
        for suffix, multiplier in [("u", 1e-6), ("n", 1e-9), ("p", 1e-12)]:
            if val >= multiplier:
                reduced = val / multiplier
                reduced = round(reduced, 6)
                if reduced.is_integer():
                    return f"{int(reduced)}{suffix}"
                return f"{reduced:.1f}{suffix}"

    # Fallback for plain numbers (e.g. 100R)
    val = round(val, 6)
    if val.is_integer():
        return str(int(val))
    return str(val)


def float_to_display_string(val: float) -> str:
    """Converts a float to BS 1852 "Sandwich" format (e.g., '1k5').

    This format is preferred for printed checklists and PCBs as it is
    more compact and harder to misread (no decimal points).

    Args:
        val: The float value (e.g., 1500.0).

    Returns:
        A string formatted as '1k5', '4u7', etc.
    """
    base = float_to_search_string(val)

    # Transform 4.7k -> 4k7
    if "." in base and any(c.isalpha() for c in base):
        num, rest = base.split(".")
        # rest contains something like '7k'
        # Split digits from letters
        match = re.search(r"(\d+)([a-zA-Z]+)", rest)
        if match:
            decimal_part = match.group(1)
            suffix = match.group(2)
            return f"{num}{suffix}{decimal_part}"

    return base


def get_clean_name(raw_key: str) -> str:
    """Parses '[Source] [Category] Name' into 'Name - Source'."""
    if not raw_key:
        return ""
    from src.bom_lib.presets import parse_preset_key

    parsed = parse_preset_key(raw_key)
    if parsed:
        src, _cat, name = parsed
        return f"{name} - {src}"
    return raw_key
