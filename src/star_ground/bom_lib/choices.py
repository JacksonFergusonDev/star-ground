"""Picks one value where a document lets the builder choose.

'1M to 2M2', '15k or 22k', '51pF (or 47pF) MLCC': the first value is bought,
and the builder is told about the others. This is a reading rule, not a check:
the value is changed.
"""

import re
from dataclasses import dataclass

from star_ground.bom_lib.utils import parse_value_to_decimal


@dataclass(frozen=True, slots=True)
class ValueChoice:
    """The value to buy, and what the builder is told about the choice.

    Attributes:
        value: The value bought.
        note: The other values the document allows, for the builder; None
            when the document offered no choice.
    """

    value: str
    note: str | None = None


# A value given as a range: 'RPD  1M to 2M2', 'CLR  1k8 - 4k7'.
_VALUE_RANGE = re.compile(
    r"(?P<lo>\d+(?:\.\d+)?[pnuµmkKMR]?\d*[FΩ]?)\s*(?:to|-|\u2013)\s*"
    r"(?P<hi>\d+(?:\.\d+)?[pnuµmkKMR]?\d*[FΩ]?)(?P<rest>(?:\s.*)?)",
    re.IGNORECASE,
)
# Two part numbers or switch types, either of which may be bought. In a
# comma list a part number may start with its digits ('7660S, 1044', GuitarPCB
# Fuzzy Bee); a slash between bare numbers is no choice ('1/4').
_PART_CODE = r"(?:\d?[A-Z]{1,4}\d{2,5}[A-Z]{0,3}|[SD34]P[SDT]T)"
_LISTED_CODE = rf"(?:{_PART_CODE}|\d{{3,5}}[A-Z]{{0,3}})"
_PART_PAIR = re.compile(
    rf"({_PART_CODE})\s*(?:/|\bor\b)\s*({_PART_CODE})"
    rf"|({_LISTED_CODE}),\s*({_LISTED_CODE})"
)
# Words describing both values may follow: '4u7 or 5uF electro' (Tonepad).
_VALUE_PAIR = re.compile(
    r"(\S+?)(?:\s+or\s+|\s*/\s*)(\S+)((?:\s+[A-Za-z][A-Za-z.]*)*)", re.IGNORECASE
)
_UNIT = re.compile(r"[A-Za-zµΩ]")
_VOLTAGE = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)
_SINGLE_REF = re.compile(r"[^\s,]+")
# Examples of a part type: 'Silicon diode (1N456, 1N4148, etc)' (Tonepad).
_EXAMPLES = re.compile(r"\s*\(([^()]*),\s*etc\.?\)\s*$", re.IGNORECASE)
# An alternative printed inside the value: '51pF (or 47pF) MLCC'.
# Tonepad: 'NP4124 (use 2N3904)', '4558 (TL072 will also work)'.
_OR_ALTERNATIVE = re.compile(
    r"\s*\(\s*(?:or|use)\s+([^()]+?)\s*\)|\s*\(([^()]+?)\s+will\s+also\s+work\)",
    re.IGNORECASE,
)


def choose_value(ref: str, value: str) -> ValueChoice:
    """Picks the first value when the document leaves the choice to the builder.

    Aion FX lists 'RPD  1M to 2M2' and '51pF (or 47pF) MLCC'; GuitarPCB lists
    'CLR  1k8 - 4k7'. The first value is bought, and the choice is surfaced so
    the builder can pick another.

    Args:
        ref: The designator.
        value: The listed value.

    Returns:
        The value with the range or alternative reduced to its first choice,
        and what the builder is told about the choice.
    """
    pair = _PART_PAIR.fullmatch(value.strip())
    if pair:
        # '2N5088/2N2222' (American Fuzz), 'SPST or SPDT' (RATT Deluxe).
        first, second = (g for g in pair.groups() if g)
        return ValueChoice(
            first, f"{ref}: the document allows {first} or {second}; {first} is used."
        )
    either = _VALUE_PAIR.fullmatch(value.strip())
    slash = " or " not in either.group(0).lower() if either else False
    if (
        either
        and all(parse_value_to_decimal(v) is not None for v in either.group(1, 2))
        and not (
            slash
            and (
                # A slash between bare numbers is a fraction ('1/4'), one
                # before a voltage is a rating ('220u/35V', Aion FX), and two
                # designators take one value each ('R21, R23 | 1k8/4k7').
                any(
                    not _UNIT.search(v) or _VOLTAGE.fullmatch(v)
                    for v in either.group(1, 2)
                )
                or not _SINGLE_REF.fullmatch(ref)
            )
        )
    ):
        # '15k or 22k' (MUFF'N Creamy Dreamer), '270nF/220nF' (Five Cats
        # Albus); not a bare '1/4'. A value restated in other units
        # ('100n/0.1uF', Aion FX Cumulus) is no choice.
        restated = len({parse_value_to_decimal(v) for v in either.group(1, 2)}) == 1
        note = (
            f"{ref}: the document allows {either.group(1)} or {either.group(2)}; "
            f"{either.group(1)} is used."
        )
        return ValueChoice(
            either.group(1) + either.group(3), None if restated else note
        )
    examples = _EXAMPLES.search(value)
    if examples:
        chosen = value[: examples.start()].strip()
        return ValueChoice(
            chosen,
            f"{ref} is a type, not a part number ('{chosen}'); the document "
            f"suggests {examples.group(1)}.",
        )
    alternative = _OR_ALTERNATIVE.search(value)
    if alternative:
        chosen = " ".join(_OR_ALTERNATIVE.sub(" ", value).split())
        return ValueChoice(
            chosen,
            f"{ref}: the document also allows "
            f"{alternative.group(1) or alternative.group(2)}; "
            f"{chosen} is used.",
        )
    m = _VALUE_RANGE.fullmatch(value.strip())
    if m:
        lo = parse_value_to_decimal(m.group("lo"))
        hi = parse_value_to_decimal(m.group("hi"))
        lo_text = m.group("lo")
        unit = re.search(r"[A-Za-zµΩ]+$", m.group("hi"))
        if unit and not re.search(r"[A-Za-zµΩ]", lo_text):
            # '22-33k' (Guitar Bass Driver) shares the upper end's unit: 22k.
            lo_text += unit.group(0)
            lo = parse_value_to_decimal(lo_text)
        has_unit = re.search(r"[A-Za-zµΩ]", m.group("hi")) is not None
        if has_unit and lo is not None and hi is not None and lo < hi:
            chosen = f"{lo_text}{m.group('rest')}".strip()
            return ValueChoice(
                chosen,
                f"{ref}: the document allows {lo_text} to {m.group('hi')}; "
                f"{lo_text} is used.",
            )
    return ValueChoice(value)
