"""Tells what a cell of a parts list holds: a designator, a value, a heading or prose.

Also cleans a value of the remarks printed beside it ('(see text)', 'Included').
"""

import re

from star_ground.bom_lib import constants
from star_ground.bom_lib.designators import BOARD_REF, is_designator, parse_designator
from star_ground.bom_lib.enums import ComponentCategory

# Unfamiliar numbered labels (CTRL1, FUZZ2) that are not standard designators.
NUMBERED_LABEL = re.compile(r"[A-Z]{1,5}\d{1,3}")
# A designator range in a listing grid: 'D1-D2  1N4148' (GuitarPCB SWAH). A bare
# upper end is short ('POT1 - 6'): 'R7 - 220' is a resistor's value, and
# 'IC1 - TL072' a part number (Tonepad); both ends share a prefix.
REF_RANGE = re.compile(
    r"((?P<prefix>[A-Z]{1,4})\d{1,3}) ?[-\u2013] ?(?:(?P=prefix)\d{1,3}|\d{1,2})"
)
# Single letters (Blue Waffle's 'V  B500K') and slashes ('BASS/MIDS') occur.
# 'GAIN.BRT' (GuitarPCB Sunn-T: the bright channel's gain) names one control.
CONTROL_NAME = re.compile(r"[A-Z](?:[A-Z0-9_/-]{1,11})?(?:\.[A-Z]{2,5})?")
# A control name in title case, recognized only beside a tapered pot value:
# 'Volume  A100k', 'Mid  B100k' (GuitarPCB Tone TwEQ).
TITLE_NAME = re.compile(r"[A-Z][a-z]{2,11}")
# 'A100k', or a taper in words: 'Volume  100k Log', 'Fuzz  10k Anti-Log **'
# (GuitarPCB Funk 49).
TAPERED_POT = re.compile(
    r"[ABCWG]\d+(?:\.\d+)?[KkM]"
    r"|\d+(?:\.\d+)?[KkM] (?i:anti-?log|rev(?:erse)? log|log|lin(?:ear)?|audio)(?: ?\*+)?"
)
# Designators side by side in one cell, 'D1 D2' (Funk 49), and a range
# spaced from its prefix, 'LDR 1 - 4' (Band of Gypsys).
_SPACED_REFS = re.compile(r"([A-Z]{1,3})\d{1,3}(?: \1\d{1,3}){1,3}")
_SPACED_RANGE = re.compile(r"([A-Z]{2,4}) (\d{1,3}) ?[-\u2013] ?(\d{1,3})")
_MULTIWORD_NAME = re.compile(r"[A-Z]{2,}(?: [A-Z]{2,}){1,2}|[A-Z]{3,} \d{1,2}")


# A pot value or switch spec, optionally led by a marker or qualifier:
# 'Dual B10K', 'Latching 3PDT Footswitch', 'On/OFF/On DPDT Toggle', '*DPDT'.
CONTROL_VALUE = re.compile(
    r"\*?(?:(?i:dual|latching|momentary|mini|slide|type-?2|on(?:\s*[/-]\s*(?:on|off))+)\s+)*"
    # Kilohms in either case: 'VOL  A100k' (GuitarPCB Blues Power).
    r"(?:[ABCWG]\d+(?:\.\d+)?[KkM][ABC]?\b|\d+(?:\.\d+)?[KkMR][ABC]?\b|[SD34]P[DS]T\b|\dP\dT\b"
    # A jack: 'SIDECHAIN  1/4" Switching TRS' (Muzzle).
    r"|(?=\S*\d.*\b(?i:trs|jack)\b)"
    # Toggle positions alone: 'SLA  ON/OFF/ON' (Trojan Horse).
    r"|(?i:\(?on(?:\s*[/-]\s*(?:on|off))+\)?)$)"
)

# Column headers and connective words in multi-column parts lists.
_LAYOUT_WORDS = frozenset(
    {
        "RESISTORS",
        "CAPACITORS",
        "SEMICONDUCTORS",
        "DIODES",
        "TRANSISTORS",
        "INTEGRATED",
        "CIRCUITS",
        "POTENTIOMETERS",
        "CRYSTALS",
        "SWITCHES",
        "SWITCH",
        "TOGGLE",
        "ROTARY",
        "LDRS",
        "OR",
    }
)
# Section headers that also appear beside values. Used only to stop a value
# from absorbing them; 'TRIM' alone is still a designator.
HEADER_WORDS = _LAYOUT_WORDS | {
    # Tonepad sets 'Schematic' and 'LAYOUT' headings on the parts list's lines.
    "SCHEMATIC",
    "LAYOUT",
    "TRIM",
    "POTS",
    "(3362P)",
    "(3362P",
    "TYPE)",
    "TRANSFORMERS",
    "OPTICAL",
    "MODIFICATIONS",
    "SMD",
    "THROUGH-HOLE",
    "DIP",
    "LEDS",
    "OPTOISOLATORS",
    "FOOTSWITCHES",
    "TUBES",
    "NUTUBE",
    "PIN",
    "HEADERS",
}


# A part description in a type column: '1/4 Watt Resistor', 'Box Cap',
# 'Electrolytic Capacitor - Min 16V', 'NPN - EBC', '16mm Potentiometer'.
_TYPE_TEXT = re.compile(
    r"\b(?:resistor|cap(?:acitor)?|diode|pot(?:entiometer)?|transistor|mosfet|j-?fet"
    r"|[np]pn|op[- ]?amp|regulator|toggle|trim(?:-?pot|mer)?|footswitch|switching"
    # A type cell OCR splits in two keeps only its first word: 'Box | Cap'.
    r"|box|ceramic|electrolytic|polari[sz]ed|mlcc|film|schottky|zenn?er|germanium"
    r"|charge pump|darlington|transformer"
    r"|watt|standard|\d+[- ]?pin|dip-?\d*)\b",
    re.IGNORECASE,
)


# A part value or part number: '33k', '2M2', '220uF', 'B5K', 'TL072', '1N4148',
# '42TM002'.
VALUE_SHAPE = re.compile(
    r"[ABCW]?\d+(?:\.\d+)?[pnuµkKMR]\d*(?:F|Ω)?|\d{0,2}[A-Za-z]{1,4}\d{2,}[A-Z]*",
)


# A function label printed between a designator and its value: 'SW1 (Power) SPDT'.
LABEL = re.compile(r"\([^()\d]*\)")


_LED_WORD = re.compile(r"\bLED\b", re.IGNORECASE)


# Two or more lowercase words mark a sentence ('Choose between ... or SMD').
_LOWERCASE_WORD = re.compile(r"[a-z][a-z'-]*[.,;:]?")
ASIDE = re.compile(r"\((?:see (?:notes?|text)|or equivalent)\)", re.IGNORECASE)
# A trailing remark naming the part this one replaces.
_OPTION_TO = re.compile(
    r"\s+(?:an? )?option (?:to|for) [A-Z]{1,4}\d{1,3}$", re.IGNORECASE
)
# A trailing unbracketed pointer to the build notes.
_SEE_TEXT = re.compile(r"\s+see (?:build )?(?:notes?|text)$", re.IGNORECASE)
# '1N5817 v4.5 only' (GuitarPCB Sriracha Fuzz): the board revision that has it.
_VERSION_ONLY = re.compile(r"\s+v\d+(?:\.\d+)? only$", re.IGNORECASE)
# Trailing words that only restate the component type.
# A part supplied with the PCB is still a part: '24LC32A *Included' (Deflector).
_INCLUDED = re.compile(r"\s*\*\s*Included(?: with (?:the )?PCB)?\s*$", re.IGNORECASE)
# 'Dip Switch' stays: a bare 'DIP2' would read as an IC package size.
_REDUNDANT_SUFFIX = re.compile(r"(?<!\bDIP)\s+(?:Crystal|Switch)$", re.IGNORECASE)


def has_digit(cell: str | None) -> bool:
    """Whether a cell holds a digit."""
    return cell is not None and any(ch.isdigit() for ch in cell)


# 'P1-Crackle', 'P2-Volume' (GuitarPCB Sho' Nuff): a control's designator, then
# its name. Not a socket ('IC1-S') or a range ('D1-D4').
_NAMED_CONTROL = re.compile(
    r"([A-Z]{1,3}\d{1,3})[-=](?![A-Z]{1,3}\d)[A-Za-z][A-Za-z/]*"
)


def control_designator(cell: str) -> str | None:
    """The designator of a control named after it ('P1-Crackle' -> 'P1').

    Args:
        cell: A cell's text.

    Returns:
        The designator, or None if the cell is no pot or switch named this way.
    """
    m = _NAMED_CONTROL.fullmatch(cell)
    designator = parse_designator(m.group(1)) if m else None
    if (
        m
        and designator is not None
        and designator.category
        in (
            ComponentCategory.POTENTIOMETERS,
            ComponentCategory.SWITCHES,
        )
    ):
        return m.group(1)
    return None


# 'VOL1 - Trim  50k' (GuitarPCB NostalgiTone Space Modulator, read by OCR): the
# part's kind after its designator.
KIND_SUFFIX = re.compile(r"(\S+) [-\u2013] (?:Trim|Trimmer|Pot)", re.IGNORECASE)


def is_ref_cell(cell: str, next_cell: str | None) -> bool:
    """Whether a cell names a part ('R1', 'DRIVE'), judged with the cell after it."""
    # An optional-part marker is not part of the name: 'TREBLE*' (Fuchsia).
    cell = unmarked(cell)
    kind = KIND_SUFFIX.fullmatch(cell)
    if kind:
        cell = kind.group(1)
    if cell in _LAYOUT_WORDS:
        return False
    if cell in constants.BARE_DESIGNATORS:
        return _is_bare_value(next_cell)
    if is_designator(cell) or NUMBERED_LABEL.fullmatch(cell):
        return True
    if control_designator(cell) is not None:
        return next_cell is not None and CONTROL_VALUE.match(next_cell) is not None
    if ref_group(cell) is not None:
        return next_cell is not None and not next_cell.isdigit()
    span = REF_RANGE.fullmatch(cell)
    # A numbered label names a range too: 'EFL1-3  Status LED' (GuitarPCB
    # NostalgiTone, read by OCR).
    if span and (
        is_designator(span.group(1)) or NUMBERED_LABEL.fullmatch(span.group(1))
    ):
        # Not before a bare count: 'D21 - D28  8' is a quantity column (Q-Tune).
        return next_cell is not None and not next_cell.isdigit()
    if (
        TITLE_NAME.fullmatch(cell)
        and next_cell is not None
        and TAPERED_POT.fullmatch(next_cell)
    ):
        return True
    if cell in constants.KEYWORDS:
        return True
    # Unfamiliar control names (SHIFT, SUBS, TONE BYPASS SWITCH) are recognized
    # by their value.
    named = (
        CONTROL_NAME.fullmatch(cell) is not None
        # A pole/throw code is a value: 'DPDT  On-On-On' (Minidrive).
        and constants.SWITCH_VALUE_PATTERN.fullmatch(cell) is None
    ) or (
        _MULTIWORD_NAME.fullmatch(cell) is not None
        and not set(cell.split()) <= HEADER_WORDS  # 'TOGGLE SWITCHES' is a header
    )
    return (
        next_cell is not None and named and CONTROL_VALUE.match(next_cell) is not None
    )


def unmarked(cell: str) -> str:
    """A designator without its see-text asterisks ('TREBLE*', '**D3')."""
    return cell.strip("*") or cell


def ref_group(cell: str) -> str | None:
    """A designator list or range written with spaces, as 'D1,D2' or 'LDR1-LDR4'."""
    if _SPACED_REFS.fullmatch(cell) and all(is_designator(r) for r in cell.split()):
        return ",".join(cell.split())
    # 'Q1/Q2  germanium' (Aion FX legacy Solaris).
    pair = cell.split("/")
    if len(pair) == 2 and all(is_designator(r) for r in pair):
        return ",".join(pair)
    span = _SPACED_RANGE.fullmatch(cell)
    if span and is_designator(span.group(1) + span.group(2)):
        prefix = span.group(1)
        return f"{prefix}{span.group(2)}-{prefix}{span.group(3)}"
    return None


def _is_bare_value(cell: str | None) -> bool:
    """Whether a cell can be the value of a bare designator ('LED', 'CLR').

    'LED' is also an ordinary value ('D3  LED'); as a designator it is followed
    by a numeric value ('LED  3mm Red LED') or an LED description that does
    not repeat it on its own ('LED  Indicator LED').
    """
    if cell is None or NUMBERED_LABEL.fullmatch(cell):
        # 'LED  POT1  B100K': the next designator is no value (Five Cats ZTrem).
        return False
    return has_digit(cell) or (
        cell.upper() != "LED" and _LED_WORD.search(cell) is not None
    )


def is_prose(cell: str) -> bool:
    """Whether a cell reads as a sentence: two or more lowercase words."""
    words = cell.split()
    lowercase = sum(1 for w in words if _LOWERCASE_WORD.fullmatch(w))
    return not cell.startswith("(") and lowercase >= 2


def is_annotation(cell: str) -> bool:
    """Whether a cell after a value may add to it, not a heading, prose or pad letters."""
    words = cell.split()
    return not (
        cell.startswith("*")
        # A heading: 'C7 - 0.001uF | Reverse Sweep:' (Tonepad's notes column).
        or cell.endswith(":")
        # A drawing's pin or pad letters: 'A B E'.
        or (len(words) > 1 and all(len(w) == 1 and w.isalpha() for w in words))
        or is_prose(cell)
        or all(w.upper() in HEADER_WORDS for w in words)
        or BOARD_REF.fullmatch(words[0]) is not None
    )


def strip_asides(value: str) -> str:
    """Removes '(see notes)', '(see text)' and '(or equivalent)' from a value."""
    return drop_remarks(" ".join(ASIDE.sub("", value).split()) or value)


def drop_remarks(value: str) -> str:
    """Removes a trailing remark that is no part of the value.

    'Status LED see text' (GuitarPCB Tight Box) points to the build notes;
    '2k Option to R9' (Sriracha Fuzz) names the part this one replaces. A value
    that is only a pointer ('See Text') stays, so it can be reported.
    """
    return _VERSION_ONLY.sub("", _OPTION_TO.sub("", _SEE_TEXT.sub("", value))) or value


_LEADING_DASH = re.compile(r"^[-\u2013]\s*(?=\d)")


def clean_value(value: str) -> str:
    """Strips markers that are not part of a purchasable value.

    Removes optional-part asterisks, '(see notes)' and '(or equivalent)' asides,
    '*Included' markers, and trailing words that only restate the component
    type ('AB38T Crystal' -> 'AB38T').

    Args:
        value: The raw value text.

    Returns:
        The cleaned value.
    """
    value = _INCLUDED.sub("", ASIDE.sub("", value)).replace("*", " ")
    value = " ".join(value.split())
    # 'C3 -10n' (GuitarPCB Dr Phybes): the dash separates the designator.
    value = _LEADING_DASH.sub("", value)
    # A trailing link or remark is not the value: 'Mini 1P8T - https://...',
    # 'SPDT (On/On) - Short shaft toggle recommended' (Mini Heterodyne).
    head, dash, tail = value.rpartition(" - ")
    if dash and head and (tail.startswith("http") or is_prose(tail)):
        value = head
    value = drop_remarks(value)
    # A leading function label is not part of the value: '(Voltage) SPDT Toggle'.
    label = LABEL.match(value)
    if label and label.end() < len(value):
        value = value[label.end() :].strip()
    return _REDUNDANT_SUFFIX.sub("", value)


def starts_description(value: str, text: str) -> bool:
    """Whether text after a value is the part's description, in its own cell.

    OCR can set a 'Value | Type' pair closer than a cell gap, or read it as
    one box: 'TL072 Dual Op Amp', 'B250K Potentiometer', '1N914  Small
    Signal, Fast Switching Diode'.
    """
    return (
        VALUE_SHAPE.fullmatch(value) is not None
        and bool(text)
        and describes(text, None)
    )


def describes(cell: str, next_cell: str | None) -> bool:
    """Whether a cell (read with the next) describes a kind of part.

    A value is never a description, though the cell after it may be one.
    """
    text = cell if next_cell is None else f"{cell} {next_cell}"
    return (
        _TYPE_TEXT.search(text) is not None
        and VALUE_SHAPE.fullmatch(cell.split()[0]) is None
    )


def names_control(text: str) -> bool:
    """Whether a cell is a control's name, perhaps numbered: 'VOL 1', 'GAIN'.

    'Trim' is not: before a value it says the part is a trimmer ('RUETZ |
    Trim 100R', GuitarPCB Doomstortion).
    """
    if not text.split():
        return False
    name, *rest = text.upper().split()
    return (
        name != "TRIM"
        and (name in constants.POT_LABELS or name in constants.KEYWORDS)
        and all(w.isdigit() for w in rest)
    )


# 'P1=FUZZ' (GuitarPCB PUFF v4) names the pot with an equals sign.
NAMED_DESIGNATOR = re.compile(r"([A-Z]{1,4}\d{1,3})[-=](?!(?i:socket)\b)[A-Za-z]{3,}")
