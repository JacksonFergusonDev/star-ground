"""Utility functions for string manipulation and numeric parsing.

This module handles the low-level formatting logic, including:
- Natural sorting (R1, R2, R10).
- Range expansion (R1-R5 -> R1, R2...).
- SI unit parsing (1k5 -> 1500.0).
- Search string generation (1500 ohms -> 1.5k).
"""

import re
from decimal import Decimal

from star_ground.bom_lib.grammar import parse_value_to_decimal
from star_ground.bom_lib.units import Unit, Value

__all__ = [
    "deduplicate_refs",
    "expand_refs",
    "is_ref_range",
    "natural_sort_key",
    "parse_value_to_decimal",
    "value_to_search_string",
]

# The most parts one designator range is expanded into: 'R1-R50' is fifty
# resistors, but a longer range is more likely a misreading than a parts list.
MAX_RANGE_PARTS = 50

# A designator range: 'R1-R4', 'C1-3', 'POT1 - 6', 'R01-R03', 'R5-R1', and
# channels of one part, 'R1A-R1D'.
_RANGE = re.compile(r"([a-zA-Z]+)(\d+)([A-Ha-h]?)\s*-\s*([a-zA-Z]+)?(\d+)([A-Ha-h]?)")
# No real designator has more than a few digits. A range with more is not
# expanded, and its numbers are never read: converting thousands of digits
# to an int passes Python's limit and raises.
_MAX_REF_DIGITS = 4
# A natural sort compares runs of digits as numbers, a bounded run at a time.
_SORT_DIGITS = re.compile(r"(\d{1,18})")


def natural_sort_key(ref: str) -> list[int | str]:
    """Generates a sort key for natural alphanumeric sorting.

    Splits strings into text and numeric chunks so that 'R10' comes
    after 'R2', rather than 'R1'. A run of more than 18 digits, which no
    designator has, is compared 18 digits at a time.

    Args:
        ref: The reference designator string.

    Returns:
        A list of mixed types (int/str) suitable for sort keys.
    """
    return [
        # `isdigit` is true for '²', which int() refuses; `\d` and int() agree
        # on decimal digits.
        int(text) if text.isdecimal() else text.upper()
        for text in _SORT_DIGITS.split(ref)
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
    """Explodes range and list strings into individual references.

    Handles ranges like 'R1-R4', 'C1-3', 'R5-R1' and 'R1A-R1D', and
    comma-separated lists of either ('R10-R12,R18'). A range of more than
    `MAX_RANGE_PARTS` parts is left as written: `is_ref_range` tells it apart.

    Args:
        ref_raw: The raw reference string (e.g., "R1-4").

    Returns:
        A list of individual references (e.g., ['R1', 'R2', 'R3', 'R4']).
        A part that cannot be expanded is returned as written.
    """
    # Typeset ranges use dashes other than '-': 'Q1<en dash>Q2' (Aion FX legacy docs).
    ref_raw = re.sub(r"[\u2012-\u2015]", "-", ref_raw)
    parts = [p.strip() for p in ref_raw.split(",")]
    if len(parts) > 1 and all(parts):
        # 'C12, 15, 16' (Q-Tune) and 'C1-C3, 5-7': a bare number or range
        # reuses the previous prefix.
        prefix = ""
        for k, part in enumerate(parts):
            m = re.match(r"[A-Za-z]+", part)
            if m:
                prefix = m.group(0)
            elif prefix and re.fullmatch(r"\d+(?:\s*-\s*\d+)?", part):
                parts[k] = prefix + part
        return [ref for part in parts for ref in _expand_range(part)]
    return _expand_range(ref_raw)


def is_ref_range(ref: str) -> bool:
    """Whether a reference is a designator range, as `expand_refs` leaves one.

    Args:
        ref: One reference from `expand_refs`.

    Returns:
        True for a range it could not expand: 'R1-R60' (over the limit),
        'R1-C3' (two kinds of part).
    """
    return _RANGE.fullmatch(ref.strip()) is not None


def _expand_range(ref_raw: str) -> list[str]:
    """Expands one range ('R1-R4', 'POT1 - 6', 'R1A-R1D'), or returns it as written."""
    ref_raw = ref_raw.strip()
    m = _RANGE.fullmatch(ref_raw)
    if m is None:
        return [ref_raw]
    prefix, first, first_letter, prefix2, last, last_letter = m.groups()
    # If a second prefix is provided, it must match the first prefix
    if prefix2 and prefix2.upper() != prefix.upper():
        return [ref_raw]
    if max(len(first), len(last)) > _MAX_REF_DIGITS:
        return [ref_raw]

    if first_letter or last_letter:
        # Channels of one part: 'R1A-R1D'.
        if first != last or not (first_letter and last_letter):
            return [ref_raw]
        lo, hi = sorted((ord(first_letter), ord(last_letter)))
        return [f"{prefix}{first}{chr(c)}" for c in range(lo, hi + 1)]

    lo, hi = sorted((int(first), int(last)))
    if hi - lo >= MAX_RANGE_PARTS:
        return [ref_raw]
    # 'R01-R03' keeps its zero padding.
    padded = any(len(n) > 1 and n.startswith("0") for n in (first, last))
    width = max(len(first), len(last)) if padded else 0
    return [f"{prefix}{i:0{width}d}" for i in range(lo, hi + 1)]


_LARGE_PREFIXES = (("M", Decimal("1e6")), ("k", Decimal("1e3")))
_SMALL_FARAD_PREFIXES = (("u", Decimal("1e-6")), ("n", Decimal("1e-9")))
_PICO = Decimal("1e-12")


def value_to_search_string(value: Value) -> str:
    """Writes a value the way parts suppliers list it (e.g., '1.5k', '100n').

    This format is optimized for searching parts suppliers (e.g., Tayda).
    Every significant digit is kept: 4K65 is 4.65k, not 4.7k. A resistance
    under one ohm is written as a plain decimal ('0.47', as '4.7' is 4.7
    ohms); a capacitance under one farad takes u, n or p ('0.5p').

    Args:
        value: The value with its unit (e.g., 1500 ohms).

    Returns:
        A string formatted with SI suffixes (k, M, u, n, p).
    """
    magnitude = value.magnitude
    size = abs(magnitude)
    for suffix, multiplier in _LARGE_PREFIXES:
        if size >= multiplier:
            return f"{_digits(magnitude / multiplier)}{suffix}"
    if value.unit == Unit.FARAD and 0 < size < 1:
        for suffix, multiplier in _SMALL_FARAD_PREFIXES:
            if size >= multiplier:
                return f"{_digits(magnitude / multiplier)}{suffix}"
        return f"{_digits(magnitude / _PICO)}p"
    return _digits(magnitude)


def _digits(number: Decimal) -> str:
    """A number's significant digits, without exponent or trailing zeros."""
    return format(number.normalize(), "f")
