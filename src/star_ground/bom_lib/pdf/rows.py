"""Helpers every PDF reader shares for its rows: labels, blanks and repeats."""

import re
from collections import Counter

from star_ground.bom_lib import constants
from star_ground.bom_lib.classifier import (
    DIP_SWITCH,
)
from star_ground.bom_lib.types import (
    ReadingStats,
)
from star_ground.bom_lib.utils import expand_refs, parse_value_to_decimal

# A named control in a grid: 'VOL', 'HI/LO', 'VOL 1', 'CLN_LVL'.
# 'VOL', 'GAIN 2'; title case too ('Input', 'Bias Trim', GuitarPCB DumbleTone).
# The second word is a number or a word, never a value: 'C1 100n' is a
# designator and its value run together (GuitarPCB ShoNuff).
# 'GAIN.BRT' (GuitarPCB Sunn-T) names one control.
CONTROL_LABEL = re.compile(
    r"[A-Z][A-Za-z0-9_/-]*(?:\.[A-Z]{2,5})?(?: (?:\d+|[A-Za-z]+))?"
)


SOCKET = re.compile(r"\bsocket\b", re.IGNORECASE)


# A capacitor's voltage rating in a cell of its own: '63V'.
RATING_CELL = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)


# The number `next_label` gives a repeated label: 'GAIN (2)'.
REPEAT = re.compile(r" \(\d+\)$")


def next_label(seen: Counter[str], ref: str) -> str:
    """The designator for a typed-table row, numbering repeats ('GAIN (2)').

    Repeats are counted per document: the tables continue across pages.
    """
    seen[ref.upper()] += 1
    return ref if seen[ref.upper()] == 1 else f"{ref} ({seen[ref.upper()]})"


def is_not_populated(value: str) -> bool:
    """Whether a row's value says the part is left off the board ('N/A', 'DNP')."""
    return value.upper() in constants.NOT_POPULATED_VALUES or bool(
        constants.NOT_POPULATED_REMARK.match(value)
    )


def record_not_populated(stats: ReadingStats, ref: str, value: str) -> None:
    """Marks a deliberately unpopulated designator as seen.

    It is not a part, but if the document lists the same designator again with
    a real value (another variant's parts list), that conflict must be flagged
    rather than silently merged into this build.
    """
    for r in expand_refs(ref):
        stats.seen_refs.record_first(r, value)


# Pin voltages beside a transistor: 'Q1  E-.39, B- .94, c- 1.62' (GuitarPCB
# ColorTone Overdrive's voltage table).
_PIN_VOLTAGES = re.compile(r"[EBCDGS]\s*[-:=]\s*\.?\d", re.IGNORECASE)


def is_ignored(value: str) -> bool:
    """Whether a row's value is no part at all: pin voltages, or a test point."""
    if _PIN_VOLTAGES.match(value):
        return True
    if DIP_SWITCH.search(value.upper()):
        return False  # a DIP switch is a part; a DIP socket line is not
    words = set(re.findall(r"[A-Z]+", value.upper()))
    phrase = " ".join(value.upper().split())
    return any(
        bad in words if " " not in bad else bad in phrase
        for bad in constants.IGNORE_VALUES
    )


ZERO_WIDTH = re.compile("[\u200b-\u200d\ufeff]")


def wrapped(ruled: str, text: str) -> bool:
    """Whether a text reading is one line of a ruled cell's wrapped value.

    GuitarPCB EMEXAR's ruled cell 'Bi-color CA LED' reads 'Bi-color CA' as
    text and Red Special's reads 'LED'; Mini-Me's 'Bi-Color CA LED (see' /
    'text)' reads the aside's tail, which the ruled reading drops.
    """
    ruled, text = (
        " ".join(ZERO_WIDTH.sub("", v).split()).upper() for v in (ruled, text)
    )
    return bool(text) and (
        ruled.startswith(text + " ")
        or ruled.endswith(" " + text)
        or (text.endswith(")") and "(" not in text)
    )


def same_value(seen: str | None, value: str) -> bool:
    """Whether two listings give one value: '100p' and '100pF', '1K8' and '1k8'.

    Zero-width spaces are ignored (GuitarPCB The Vibe's ruled cells carry them).
    """
    if seen is None:
        return False
    seen, value = (" ".join(ZERO_WIDTH.sub("", v).split()) for v in (seen, value))
    if seen.upper() == value.upper():
        return True
    a, b = seen.split(), value.split()
    if not a or not b or [w.upper() for w in a[1:]] != [w.upper() for w in b[1:]]:
        return False
    da, db = parse_value_to_decimal(a[0]), parse_value_to_decimal(b[0])
    return da is not None and da == db
