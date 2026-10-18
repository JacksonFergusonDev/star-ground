"""Parser self-checks that turn silent failures into user-facing warnings.

The extraction heuristics in `parser.py` will never be right for every
document. These checks are deliberately independent of those heuristics: they
look at the source text and the finished inventory from a different angle and
report every place where the two disagree. The invariant they enforce is that
the parser may be wrong, but it must never be wrong *silently*.
"""

import re
import unicodedata
from decimal import Decimal

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
_TRIM_WORD = re.compile(r"\bTRIM(?:MER)?\b", re.IGNORECASE)
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
    if tok == nxt:
        # 'R24 R24 is only available on v3 PCB or earlier' (MUFF'N): a remark.
        return False
    if _is_ref(tok) and _is_ref(nxt) and not any(ch.isdigit() for ch in tok):
        # 'R12 1k8* CLR TR1 100R*': a bare word naming the previous part's
        # role, not a listing of its own.
        return False
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
        # 'between J3 and J4' is prose, not 'J3' listed as 'and'.
        return (nxt[0].isalnum() or nxt[0] == "(") and nxt not in _PROSE_WORDS
    return (
        _CONTROL_NAME.fullmatch(tok) is not None
        and _CONTROL_VALUE.match(nxt) is not None
    )


_PROSE_WORDS = frozenset(
    {
        "and",
        "or",
        "to",
        "the",
        "is",
        "are",
        "was",
        "with",
        "on",
        "in",
        "of",
        "for",
        "through",
        "also",
        "as",
        "at",
        "from",
        "be",
        "will",
        "if",
        "then",
        "should",
    }
)


def _is_sentence(tokens: list[str]) -> bool:
    """Whether a line is prose: mostly lowercase words, which a parts list never is."""
    words = [
        w for t in tokens if (w := t.strip(".,;:!?()\"'")).isalpha() and w.islower()
    ]
    return len(tokens) >= _SENTENCE_TOKENS and len(words) >= 0.5 * len(tokens)


_SENTENCE_TOKENS = 8
_JUMPER_PROSE = re.compile(r"\bjump(?:er(?:ed)?|ing)?\b", re.IGNORECASE)


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
        if _JUMPER_PROSE.search(line):
            # 'Always jump J1 through J4': jumper pads, not parts.
            continue
        tokens = line.split()
        if _is_sentence(tokens):
            # 'simply attaching J4 wiring to the Tone TwEQ Volume pad 3 instead.'
            continue
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
    # The words of a two-word label are accounted for ('HI CUT' covers 'CUT').
    extracted |= {word for ref in extracted if " " in ref for word in ref.split()}

    listed: set[str] = set()
    for text in page_texts:
        # A vector schematic labels every symbol 'REF value'; those labels are
        # a drawing, not a parts list.
        if not _SCHEMATIC_PAGE.search(text):
            listed |= find_listed_refs(text)

    # A part number shaped like a designator is a value, not a listing:
    # 'Q1-Q5  J201  JFET transistor' (Golden Falk).
    values = {key.value.split()[0].upper() for key in inventory if key.value.split()}
    if "LED" in extracted and "LED2" not in {r.upper() for r in listed}:
        # A bare 'LED' in the parts list is the schematic's only 'LED1'
        # (Aion FX legacy documents).
        extracted.add("LED1")
    for ref in sorted(r for r in listed if r.upper() not in extracted):
        if ref.upper() in values:
            continue
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


_CAPACITOR_CODE = re.compile(r"(\d{2})(\d)")


def check_marking_code(stats: StatsDict, ref: str, value: str, marking: str) -> None:
    """Warns when a capacitor's printed value disagrees with its marking code.

    Q-Tune lists C3 as '330 (334)': a bare 330 is 330pF, but code 334 is
    33 x 10^4 pF = 330nF. One of the two is a misprint, and the builder must
    know which part to use.

    Args:
        stats: Stats object that receives the warning.
        ref: The capacitor's designator.
        value: The printed value ('330', '100n').
        marking: The marking printed beside it ('334').
    """
    code = _CAPACITOR_CODE.fullmatch(marking)
    farads = parse_value_to_decimal(value)
    if code is None or farads is None:
        return
    if not any(ch.isalpha() for ch in value):
        farads *= Decimal("1e-12")  # a bare capacitor value is in picofarads
    coded = int(code.group(1)) * Decimal(10) ** int(code.group(2)) * Decimal("1e-12")
    if abs(farads - coded) > coded * Decimal("0.01"):
        _warn(
            stats,
            ParseWarningKind.BOM_MISMATCH,
            ref,
            f"{ref} is listed as '{value}' but marked '{marking}' "
            f"({float(coded):.3g} F); check which value to use.",
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


def check_second_board(
    stats: StatsDict, ref: str, second: str, first_value: str, second_value: str
) -> None:
    """Tells the builder a designator is listed in two tables with two values.

    Both parts are bought: two boards in one document repeat their
    designators (Tweed Fuzztone), but so might two versions of one circuit.

    Args:
        stats: Stats object that receives the warning.
        ref: The repeated designator.
        second: The name the second listing was added under ('R1 (2)').
        first_value: The first table's value.
        second_value: The other table's value.
    """
    _warn(
        stats,
        ParseWarningKind.CONFLICTING_DUPLICATE,
        ref,
        f"{ref} is listed in two tables, as '{first_value}' and '{second_value}'; both "
        f"were added (the second as {second}). Check whether these are two boards or "
        "two versions of the circuit.",
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


# Notes that tie a value to one named build of the circuit. Historical remarks
# ('Original uses MA856', 'Early units used 10k') name no build to choose.
# 'Early units used 10k' is history, but 'Early G-2 uses 620R' names a build.
_HISTORY = (
    r"(?!(?i:the )?(?:(?i:original|older|some|most|many|factory|can|could|may|you)\b"
    r"|(?i:early|earlier|later)\b(?! [\w-]*\d)))"
)
# A build's name: words without a sentence-ending period ('TS-9', 'Tube Sound Fuzz').
_NAME_WORD = r"(?:[\w/-]|\.(?=\w))+"
_VERSION_NOTE = re.compile(
    rf"\b(?i:in|for) (?:the )?{_HISTORY}(?!(?i:this|stock|standard|any|all|every)\b)"
    r"[\w' /.-]+? (?i:version|variant|specs?|circuit)\b"
    # 'Use a 100n film capacitor here if building a 3-transistor variant'.
    r"|\b(?i:if building)\b"
    # 'Analogman version uses 1.2M here', 'GC-1 uses 150k', 'G-2 uses 22uF'.
    rf"|(?:^|[.;]\s*){_HISTORY}(?:[A-Z]{_NAME_WORD}?(?: {_NAME_WORD}){{0,3}}) uses \S"
    # 'Omit (leave empty) for P-2', 'Jumper for GC-1', 'Omit if using Morning Glory'.
    rf"|\b(?i:omit|jumper)\b[^.;]*?\b(?i:for|in|if using) {_HISTORY}(?:the )?[A-Z]"
    # '100pF in the SD-9M', '10M in Catalinbread and Orpheum'.
    rf"|\bin the {_HISTORY}[A-Z][\w-]*\d"
    rf"|^\S*\d\S* (?:in|for) {_HISTORY}[A-Z]"
    # 'Orpheum: 10M; Axis/Foxey: omit (leave empty)'. Not a generic label:
    # 'Alternate: 4K7', 'Sub: MPSA18', 'Quantity: 2' (PedalPCB) name no build.
    r"|^(?!(?i:alt(?:ernat(?:e|ive))?|sub(?:stitute)?|quantity|qty|note|notes|"
    r"use|or|optional|alternate part|recommended|see)\s*:)"
    r"[A-Z][\w &/'-]{1,30}: [\w.]+(?:\s*[;.]|$)"
    # 'Part of the optional Morning Glory gain stage.'
    r"|\b(?i:part of the optional)\b",
)


def check_version_note(stats: StatsDict, ref: str, notes: str) -> None:
    """Surfaces a parts-table note that ties a part to one version of the circuit.

    Lot Lizard's NOTES say '* Omitted in Nano version' and '* C1K in Big Box
    Version'; Aion FX's say 'GC-1 uses 150k', '150k for silicon version' and
    'Omit if using Morning Glory gain stage': the list is one table for several
    builds, and which parts to buy depends on the build.

    Args:
        stats: Stats object that receives the warning.
        ref: The row's designator.
        notes: The row's NOTES cell.
    """
    note = " ".join(notes.replace("*", " ").split())
    if _VERSION_NOTE.search(note):
        _warn(
            stats,
            ParseWarningKind.FOOTNOTE,
            ref,
            f"{ref}: '{note}'. The document describes more than one version; "
            "check which parts your build needs.",
        )


def check_unclassified_row(stats: StatsDict, ref: str, value: str) -> None:
    """Warns about a listed part that no category fits.

    A parts table lists only parts, so a row the classifier cannot place
    (ElectroVibe's 'LMP1 | 2174 | Incandescent lamp') must not vanish from
    the shopping list unnoticed; nor may a numbered label beside a part
    number (Trumpeter's 'IQ1  2N5088').

    Args:
        stats: Stats object that receives the warning.
        ref: The row's designator.
        value: The row's value.
    """
    _warn(
        stats,
        ParseWarningKind.UNPARSED_VALUE,
        ref,
        f"{ref} '{value}' is listed but was not recognized as a part; add it by hand.",
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
        # An optoisolator may carry an IC designator (Constrictor: 'U1  VTL5C10').
        vactrol = (
            expected == ComponentCategory.ICS
            and category == ComponentCategory.OPTOELECTRONICS
        )
        # A trimmer under a resistor designator (CDXL Reissue: 'R20  10K TRIM').
        trimmer = (
            expected == ComponentCategory.RESISTORS
            and category == ComponentCategory.POTENTIOMETERS
            and _TRIM_WORD.search(raw_value) is not None
        )
        if expected is not None and category != expected and not (vactrol or trimmer):
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
        if key.value.upper() == "LED":
            # Distortr lists 'D3  LED'; its bill of materials says '3mm Red LED'.
            named = [e for e in entries if "LED" in e.upper() and e.upper() != "LED"]
            if named:
                for ref in data["refs"]:
                    _warn(
                        stats,
                        ParseWarningKind.BOM_MISMATCH,
                        ref,
                        f"{ref} is listed only as 'LED'; the document's bill of "
                        f"materials gives '{named[0]}'.",
                    )
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


# A value given as a range: 'RPD  1M to 2M2', 'CLR  1k8 - 4k7'.
_VALUE_RANGE = re.compile(
    r"(?P<lo>\d+(?:\.\d+)?[pnuµmkKMR]?\d*[FΩ]?)\s*(?:to|-|\u2013)\s*"
    r"(?P<hi>\d+(?:\.\d+)?[pnuµmkKMR]?\d*[FΩ]?)(?P<rest>(?:\s.*)?)",
    re.IGNORECASE,
)
# Two part numbers or switch types, either of which may be bought.
_PART_CODE = r"(?:\d?[A-Z]{1,4}\d{2,5}[A-Z]{0,3}|[SD34]P[SDT]T)"
_PART_PAIR = re.compile(rf"({_PART_CODE})\s*(?:/|\bor\b)\s*({_PART_CODE})")
_VALUE_PAIR = re.compile(r"(\S+?)(?:\s+or\s+|\s*/\s*)(\S+)", re.IGNORECASE)
_UNIT = re.compile(r"[A-Za-zµΩ]")
_VOLTAGE = re.compile(r"\d+(?:\.\d+)?V", re.IGNORECASE)
_SINGLE_REF = re.compile(r"[^\s,]+")
# An alternative printed inside the value: '51pF (or 47pF) MLCC'.
_OR_ALTERNATIVE = re.compile(r"\s*\(\s*or\s+([^()]+?)\s*\)", re.IGNORECASE)


def resolve_value_choice(stats: StatsDict | None, ref: str, value: str) -> str:
    """Picks the first value when the document leaves the choice to the builder.

    Aion FX lists 'RPD  1M to 2M2' and '51pF (or 47pF) MLCC'; GuitarPCB lists
    'CLR  1k8 - 4k7'. The first value is bought, and the choice is surfaced so
    the builder can pick another.

    Args:
        stats: Stats object that receives the warning, if any.
        ref: The designator.
        value: The listed value.

    Returns:
        The value with the range or alternative reduced to its first choice.
    """
    pair = _PART_PAIR.fullmatch(value.strip())
    if pair:
        # '2N5088/2N2222' (American Fuzz), 'SPST or SPDT' (RATT Deluxe).
        if stats is not None:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref}: the document allows {pair.group(1)} or {pair.group(2)}; "
                f"{pair.group(1)} is used.",
            )
        return pair.group(1)
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
        if stats is not None and not restated:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref}: the document allows {either.group(1)} or {either.group(2)}; "
                f"{either.group(1)} is used.",
            )
        return either.group(1)
    alternative = _OR_ALTERNATIVE.search(value)
    if alternative:
        chosen = " ".join(_OR_ALTERNATIVE.sub(" ", value).split())
        if stats is not None:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref}: the document also allows {alternative.group(1)}; "
                f"{chosen} is used.",
            )
        return chosen
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
            if stats is not None:
                _warn(
                    stats,
                    ParseWarningKind.VALUE_CHOICE,
                    ref,
                    f"{ref}: the document allows {lo_text} to {m.group('hi')}; "
                    f"{lo_text} is used.",
                )
            return chosen
    return value


def check_parts_found(stats: StatsDict, parts_found: int) -> None:
    """Tells the builder when no parts list could be read at all.

    Some GuitarPCB documents print their parts table as an image (SWAH v3,
    DVF), and some lists name no designators (PedalPCB ProtoBoard's 'QTY |
    PART NUMBER'): an empty shopping list must not look like a complete one.

    Args:
        stats: Stats object that receives the warning.
        parts_found: How many parts were extracted from the document.
    """
    if parts_found == 0:
        _warn(
            stats,
            ParseWarningKind.NO_PARTS_LIST,
            "",
            "No parts with designators could be read from this document: its parts "
            "list may be printed as an image, or list parts without designators. "
            "Enter the parts by hand.",
        )
