"""Parser self-checks that turn silent failures into user-facing warnings.

The extraction heuristics in `parser.py` will never be right for every
document. These checks are deliberately independent of those heuristics: they
look at the source text and the finished inventory from a different angle and
report every place where the two disagree. The invariant they enforce is that
the parser may be wrong, but it must never be wrong *silently*.
"""

import re
import unicodedata

from src.bom_lib import constants
from src.bom_lib.enums import ComponentCategory, ParseWarningKind
from src.bom_lib.types import Inventory, ParseWarning, StatsDict
from src.bom_lib.utils import parse_value_to_decimal

# Numbered designators (R1, IC10, LED2) and the bare ones PedalPCB uses (LED, CLR).
# Suffixes are channel letters (R1A, D10a) or names (IC_A); K/M/R are excluded so
# a C-taper pot value (C10K) is not read as a designator. JP (jumper pads) is
# deliberately absent: a solder jumper setting is not a part.
_REF_TOKEN = re.compile(
    r"(?:(?:R|C|D|Q|U|IC|SW|LED|LDR|X|Y|J|VR|TRIM|TR|CLR|L|T|K)\d+[A-Ha-h]?"
    r"|(?:IC|LED|JACK|SW)_[A-Za-z0-9]+"
    r"|LED[A-H]?|RLED|CLR|TRIM)"
)

# A value that can only belong to a potentiometer or a switch. Lets us spot
# named controls (SHIFT B25K, SUBS SPDT) without a whitelist of control names.
_CONTROL_NAME = re.compile(r"[A-Z][A-Z0-9]{1,11}")
_CONTROL_VALUE = re.compile(
    r"(?:[ABCWG]\d+(?:\.\d+)?[KM]\b"
    r"|\d+(?:\.\d+)?[KM][ABC]?\b"
    r"|[SD34]P[DS]T\b"
    r"|\dP\dT\b)"
)

_LED_DESCRIPTOR = re.compile(
    r"\d+(?:\.\d+)?mm|RED|GREEN|BLUE|YELLOW|WHITE|ORANGE|AMBER|PURPLE|PINK",
    re.IGNORECASE,
)

# The title block of a schematic exported from an EDA tool.
_SCHEMATIC_PAGE = re.compile(r"\bKiCad\b|\.kicad_sch\b|\bFile:\s*\S+\.sch\b", re.I)
_IC_SECTION = re.compile(r"(?P<chip>(?:IC|U)\d+)[A-H]")

_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")

# Designator prefixes whose category is unambiguous.
_PREFIX_CATEGORIES: list[tuple[str, ComponentCategory]] = [
    ("LED", ComponentCategory.DIODES),
    ("IC", ComponentCategory.ICS),
    ("R", ComponentCategory.RESISTORS),
    ("C", ComponentCategory.CAPACITORS),
    ("D", ComponentCategory.DIODES),
    ("Q", ComponentCategory.TRANSISTORS),
    ("U", ComponentCategory.ICS),
]
_NUMBERED_REF = re.compile(r"^(?P<prefix>[A-Z]+)\d+$")
_SWITCH_VALUE = re.compile(
    r"\b(?:[SD3-4]P[DS]T|\dP\dT|ON\s*[/-]\s*(?:OFF\s*[/-]\s*)?ON)\b"
)


def _comparable(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).upper()
    return re.sub(r"[^A-Z0-9]", "", folded)


def _warn(stats: StatsDict, kind: ParseWarningKind, ref: str, message: str) -> None:
    stats["warnings"].append(ParseWarning(kind=kind, ref=ref, message=message))


def _is_ref(token: str) -> bool:
    return _REF_TOKEN.fullmatch(token) is not None


def _listing_at(tokens: list[str], i: int) -> bool:
    """Whether tokens[i] is a designator immediately followed by its value."""
    if i + 1 >= len(tokens):
        return False
    tok, nxt = tokens[i], tokens[i + 1]
    if _is_ref(tok) and _is_ref(nxt):
        # 'Q4 J201 JFET' is a listing whose value looks like a designator;
        # 'R13 C4 R12' is a silkscreen run.
        return i + 2 < len(tokens) and not _is_ref(tokens[i + 2])
    if nxt.upper() in constants.NOT_POPULATED_VALUES:
        return False  # 'D3 N/A' is deliberately not a part
    if _is_ref(tok):
        # Bare designators (LED, TRIM) are also ordinary words, so demand a
        # numeric value after them. Numbered ones only need a word-like value.
        if not any(ch.isdigit() for ch in tok):
            # 'TRIM (3362P)' describes a trimmer; it is not a listing.
            return any(ch.isdigit() for ch in nxt) and not nxt.startswith("(")
        return nxt[0].isalnum() or nxt[0] == "("
    return (
        _CONTROL_NAME.fullmatch(tok) is not None
        and _CONTROL_VALUE.match(nxt) is not None
    )


def find_listed_refs(text: str) -> set[str]:
    """Finds every designator the text lists alongside a value.

    Silkscreen text extracted from board drawings is runs of bare designators
    ('R13 C4 R12'), so a designator only counts when a value follows it.

    Args:
        text: Raw text extracted from a document page.

    Returns:
        Designators that appear as 'REF value' pairs.
    """
    found: set[str] = set()
    for line in unicodedata.normalize("NFKC", text).splitlines():
        tokens = line.split()
        i = 0
        # Index just past the last listing's value: where a new listing in
        # the next column may start.
        boundary = 0
        while i < len(tokens):
            # 'Q4 J201 JFET': a designator-shaped token right after a
            # designator is that designator's value, not a listing of its own.
            after_ref = i > 0 and _is_ref(tokens[i - 1])
            # A bare designator is also an ordinary word ('3mm Red LED 3mm',
            # '• LED 1'), so it only counts where a listing can begin.
            bare = _is_ref(tokens[i]) and not any(ch.isdigit() for ch in tokens[i])
            # 'D1 3mm LED 3mm Red LED': an LED after a size or colour finishes
            # that description.
            describes = i > 0 and _LED_DESCRIPTOR.fullmatch(tokens[i - 1]) is not None
            if (
                not after_ref
                and not (bare and (i != boundary or describes))
                and _listing_at(tokens, i)
            ):
                found.add(tokens[i])
                i += 2  # the value is never itself a designator
                boundary = i
            else:
                i += 1
    return found


def check_unextracted_refs(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about designators listed in the document but absent from the output.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    extracted = {
        ref.upper()
        for data in inventory.values()
        for ref in data["refs"]
        if "(Inj)" not in ref
    }
    listed: set[str] = set()
    for text in page_texts:
        # A vector schematic labels every symbol 'REF value'; those labels are
        # a drawing, not a parts list.
        if not _SCHEMATIC_PAGE.search(text):
            listed |= find_listed_refs(text)

    for ref in sorted(r for r in listed if r.upper() not in extracted):
        section = _IC_SECTION.fullmatch(ref.upper())
        if section and section.group("chip") in extracted:
            # 'IC2A' on a schematic is one half of the dual op-amp IC2.
            continue
        _warn(
            stats,
            ParseWarningKind.UNEXTRACTED_REF,
            ref,
            f"{ref} is listed in the document but is missing from the parts list.",
        )


def check_row_quantity(
    stats: StatsDict, refs: str, ref_count: int, quantity: int | None
) -> None:
    """Warns when a grouped row's quantity disagrees with its designators.

    Args:
        stats: Stats object that receives the warning.
        refs: The designator cell as printed ('R10-R12,R18').
        ref_count: How many designators the cell expands to.
        quantity: The row's quantity cell, if any.
    """
    if quantity is not None and quantity != ref_count:
        _warn(
            stats,
            ParseWarningKind.BOM_MISMATCH,
            refs,
            f"{refs} lists {ref_count} designators but a quantity of {quantity}.",
        )


def check_footnotes(page_texts: list[str], stats: StatsDict) -> None:
    """Surfaces a footnote attached to a listed value with an asterisk.

    Super Heterodyne lists 'OSC-ROOT *DPDT On/On/On' and repeats '*DPDT On/On/On'
    below the parts list as the heading of a note: 'This switch MUST be a Type-2
    DPDT On/On/On toggle switch.' The note changes what must be bought but is
    prose, so it is shown to the builder rather than folded into the value.

    Args:
        page_texts: Raw text of every page.
        stats: Stats object that receives the warnings.
    """
    for text in page_texts:
        lines = [" ".join(line.split()) for line in text.splitlines()]
        for k, heading in enumerate(lines):
            if not heading.startswith("*") or k + 1 >= len(lines):
                continue
            body = heading.lstrip("*").strip()
            if not body:
                continue
            for line in lines:
                m = re.match(rf"(\S+) \*{re.escape(body)}$", line)
                if m and line != heading:
                    _warn(
                        stats,
                        ParseWarningKind.FOOTNOTE,
                        m.group(1),
                        f"{m.group(1)} ({body}) has a note: {lines[k + 1]}",
                    )


def check_duplicate(
    stats: StatsDict, ref: str, first_value: str, new_value: str
) -> None:
    """Warns when a designator is listed again with a different value.

    Args:
        stats: Stats object that receives the warning.
        ref: The repeated designator.
        first_value: The value that was kept.
        new_value: The value that was discarded.
    """
    looks_like_prose = new_value.isalpha() and new_value.islower()
    if looks_like_prose or _DATE.search(new_value):
        return
    if _comparable(first_value) != _comparable(new_value):
        _warn(
            stats,
            ParseWarningKind.CONFLICTING_DUPLICATE,
            ref,
            f"{ref} is listed as both '{first_value}' and '{new_value}'; "
            f"only '{first_value}' was used.",
        )


def check_unclassified_row(stats: StatsDict, ref: str, value: str) -> None:
    """Warns about a parts-table row that no category fits.

    A ruled table lists only parts, so a row the classifier cannot place
    (ElectroVibe's 'LMP1 | 2174 | Incandescent lamp') must not vanish from
    the shopping list unnoticed.

    Args:
        stats: Stats object that receives the warning.
        ref: The row's designator.
        value: The row's value.
    """
    _warn(
        stats,
        ParseWarningKind.UNPARSED_VALUE,
        ref,
        f"{ref} '{value}' is in the parts table but was not recognized as a part; "
        "add it by hand.",
    )


def check_classification(
    stats: StatsDict, ref: str, raw_value: str, category: ComponentCategory, value: str
) -> None:
    """Warns when a classified part contradicts its own designator or value.

    Args:
        stats: Stats object that receives the warnings.
        ref: The designator.
        raw_value: The value exactly as extracted.
        category: The category the classifier chose.
        value: The normalized value the classifier produced.
    """
    if not any(ch.isalnum() for ch in raw_value):
        # A placeholder ('***' with "Refer to Page 3") names no part at all.
        _warn(
            stats,
            ParseWarningKind.UNPARSED_VALUE,
            ref,
            f"{ref} value '{raw_value}' is a placeholder, not a part; "
            "check the document for what to use.",
        )
        return

    m = _NUMBERED_REF.match(ref.upper())
    if m:
        prefix = m.group("prefix")
        expected = next((cat for p, cat in _PREFIX_CATEGORIES if prefix == p), None)
        if expected is not None and category != expected:
            _warn(
                stats,
                ParseWarningKind.CATEGORY_CONFLICT,
                ref,
                f"{ref} ('{raw_value}') was classified as {category.value}, "
                f"but its designator indicates {expected.value}.",
            )
            return

    if (
        _SWITCH_VALUE.search(raw_value.upper())
        and category != ComponentCategory.SWITCHES
    ):
        _warn(
            stats,
            ParseWarningKind.CATEGORY_CONFLICT,
            ref,
            f"{ref} ('{raw_value}') looks like a switch but was classified as "
            f"{category.value}.",
        )
        return

    if category in (ComponentCategory.RESISTORS, ComponentCategory.CAPACITORS):
        head = value.split()[0] if value.split() else value
        if "mm" not in value.lower() and parse_value_to_decimal(head) is None:
            _warn(
                stats,
                ParseWarningKind.UNPARSED_VALUE,
                ref,
                f"{ref} value '{raw_value}' is not a recognizable "
                f"{category.value.lower()[:-1]} value.",
            )


_BOM_HEADING = re.compile(r"bill\s*of\s*materials", re.IGNORECASE)
_BOM_ENTRY = re.compile(r"(?<![\d.])(\d+)\s*x\s+(.+?)(?=\s+\d+\s*x\s|$)")
# A quantity BOM needs this many 'N x VALUE' entries before it is trusted.
_MIN_BOM_ENTRIES = 5


def _value_tokens(value: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKC", value).upper()
    return frozenset(t for t in re.split(r"[^A-Z0-9.]+", folded) if t)


def _values_agree(part_value: str, bom_value: str) -> bool:
    head_a, head_b = part_value.split()[0], bom_value.split()[0]
    dec_a, dec_b = parse_value_to_decimal(head_a), parse_value_to_decimal(head_b)
    if dec_a is not None and dec_b is not None:
        return dec_a == dec_b
    a, b = _value_tokens(part_value), _value_tokens(bom_value)
    return bool(a) and bool(b) and (a <= b or b <= a)


def find_bom_entries(page_texts: list[str]) -> list[str]:
    """Extracts the values from 'Bill of Materials' pages ('3 x BS170').

    Args:
        page_texts: Raw text of every page.

    Returns:
        BOM values, or an empty list when the document has no quantity BOM.
    """
    entries = [
        m.group(2).strip()
        for text in page_texts
        if _BOM_HEADING.search(text)
        for line in text.splitlines()
        for m in _BOM_ENTRY.finditer(line)
    ]
    return entries if len(entries) >= _MIN_BOM_ENTRIES else []


def check_bom_reconciliation(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about parts whose value is absent from the document's quantity BOM.

    Older PedalPCB documents list parts twice: by designator on page 1 and by
    quantity on a separate bill of materials. The second list often names the
    part more precisely ('9v1 Zener' vs '1N4739A'), so disagreement means the
    extracted value may not be what the builder needs to buy.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    entries = find_bom_entries(page_texts)
    if not entries:
        return
    for key, data in inventory.items():
        if key.category in (ComponentCategory.PCB, ComponentCategory.HARDWARE_MISC):
            continue
        if any(_values_agree(key.value, entry) for entry in entries):
            continue
        for ref in data["refs"]:
            _warn(
                stats,
                ParseWarningKind.BOM_MISMATCH,
                ref,
                f"{ref} was read as '{key.value}', which does not appear in the "
                "document's bill of materials.",
            )
