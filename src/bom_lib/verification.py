"""Parser self-checks that turn silent failures into user-facing warnings.

The extraction heuristics in `parser.py` will never be right for every
document. These checks are deliberately independent of those heuristics: they
look at the source text and the finished inventory from a different angle and
report every place where the two disagree. The invariant they enforce is that
the parser may be wrong, but it must never be wrong *silently*.
"""

import re
import unicodedata
from collections import Counter
from decimal import Decimal

from src.bom_lib import constants
from src.bom_lib.designators import is_designator
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
# Aion FX heads its schematic pages 'SCHEMATIC'.
_SCHEMATIC_HEADING = re.compile(r"\s*SCHEMATIC\b")
_SCHEMATIC_TITLE = re.compile(r"\s*SCHEMATIC\b", re.IGNORECASE)


def is_schematic_page(text: str) -> bool:
    """Whether a page is a schematic drawing, whose 'R1 10k' labels list no parts.

    Args:
        text: The page's text.

    Returns:
        True for a KiCad schematic or a page headed 'SCHEMATIC'.
    """
    return bool(_SCHEMATIC_PAGE.search(text) or _SCHEMATIC_HEADING.match(text))


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


# A line printed on this many pages is a running header or footer.
_RUNNING_PAGES = 3


def _page_free(line: str) -> str:
    """A line without its page number: 'L5 PREAMP 5' -> 'L5 PREAMP'."""
    return re.sub(r"\s*\d+$", "", " ".join(line.split()))


def _running_lines(page_texts: list[str]) -> set[str]:
    """Lines repeated on most pages, page numbers aside: headers and footers.

    Aion FX legacy L5 Preamp's footer 'L5 PREAMP 5' reads as the designator L5
    listed with a value.
    """
    counts: Counter[str] = Counter()
    for text in page_texts:
        counts.update({_page_free(line) for line in text.splitlines() if line.strip()})
    pages = sum(1 for text in page_texts if text.strip())
    return {
        line
        for line, n in counts.items()
        if line and n >= max(_RUNNING_PAGES, pages // 2 + 1)
    }


def check_unextracted_refs(
    page_texts: list[str],
    inventory: Inventory,
    stats: StatsDict,
    omitted: frozenset[str] = frozenset(),
) -> None:
    """Warns about designators listed in the document but absent from the output.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
        omitted: Designators the parts list itself leaves empty ('C1 | OMIT');
            a schematic still labels them (Aion FX Gale).
    """
    extracted = {
        ref.upper()
        for data in inventory.values()
        for ref in data["refs"]
        if "(Inj)" not in ref
    } | {ref.upper() for ref in omitted}
    # The words of a two-word label are accounted for ('HI CUT' covers 'CUT').
    extracted |= {word for ref in extracted if " " in ref for word in ref.split()}

    listed: set[str] = set()
    # The running title also heads the first pages ('Overview L5 Preamp').
    running = [
        re.compile(rf"\b{re.escape(line)}\b", re.IGNORECASE)
        for line in _running_lines(page_texts)
    ]
    for text in page_texts:
        # A vector schematic labels every symbol 'REF value'; those labels are
        # a drawing, not a parts list.
        if not _SCHEMATIC_PAGE.search(text):
            for pattern in running:
                text = pattern.sub(" ", text)
            found = find_listed_refs(text)
            if _SCHEMATIC_TITLE.match(text):
                # A drawn schematic's net labels are no parts: Aion FX legacy
                # Astra's 'IN' sits beside R1's '1M'. Its designators still count.
                found = {ref for ref in found if any(ch.isdigit() for ch in ref)}
            listed |= found

    # A part number shaped like a designator is a value, not a listing:
    # 'Q1-Q5  J201  JFET transistor' (Golden Falk).
    values = {key.value.split()[0].upper() for key in inventory if key.value.split()}
    listed_upper = {r.upper() for r in listed}
    for bare in [r for r in extracted if r.isalpha()]:
        if f"{bare}2" not in listed_upper:
            # A bare 'LED' in the parts list is the schematic's only 'LED1' (Aion
            # FX legacy documents); 'SW - SPDT' is the notes' only 'SW1'
            # (GuitarPCB NostalgiTone Compressor).
            extracted.add(f"{bare}1")
    joined = " ".join(" ".join(page_texts).split())
    for ref in sorted(r for r in listed if r.upper() not in extracted):
        if ref.upper() in values:
            continue
        # An alternative to a listed value: 'Q1  J309 or J113' (GuitarPCB
        # FrontStage) names a JFET, not a jack.
        if any(
            m.group(1).upper() in values
            for m in re.finditer(
                rf"(\S+)\s*(?:\bor\b|/)\s*{re.escape(ref)}\b", joined, re.IGNORECASE
            )
        ):
            continue
        if not any(ch.isdigit() for ch in ref) and any(
            m.group(1).upper() in extracted
            for m in re.finditer(
                rf"\b([A-Z]{{1,3}}\d{{1,3}})\s+{re.escape(ref)}\b",
                joined,
                re.IGNORECASE,
            )
        ):
            # 'P1 GAIN 1M Log' (GuitarPCB Over Drive Circuit): the name labels P1.
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


def check_valueless_refs(
    refs: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about designators a parts table lists with no value.

    Args:
        refs: Designators printed in a designator column with no value beside
            them (`layout.valueless_refs`).
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    extracted = {ref.upper() for data in inventory.values() for ref in data["refs"]}
    for ref in dict.fromkeys(refs):
        if ref.upper() not in extracted:
            _warn(
                stats,
                ParseWarningKind.UNEXTRACTED_REF,
                ref,
                f"{ref} is listed with no value (perhaps the builder's choice): "
                "check the document.",
            )


# A series ('R1' ... 'R43') listed this sparsely, in a reading with no
# capacitors, is not the parts list at all (GuitarPCB G2 Animal's text layer
# holds only a table of substitute values; the parts list is an image). With
# capacitors listed, sparse numbering is the design's own (PedalPCB Thermionic
# Deluxe numbers its resistors R32-R37).
_SERIES_REF = re.compile(r"([A-Z]{1,3})(\d{1,3})")
_SPARSE_SHARE = 0.5
_SPARSE_MIN = 8
_SERIES = 100


def check_numbering_gaps(inventory: Inventory, stats: StatsDict) -> None:
    """Warns about the numbers missing from a designator series listed sparsely.

    Only a reading with no capacitors is checked: every circuit has some, so
    their absence says the parser read something other than the parts list.
    The document as a whole is then warned too, so the parts the reading never
    named are not missed silently (GuitarPCB G2 Animal v3 pictures its parts
    list; the text layer holds only a table of substitute values).

    Args:
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    if any(key.category == ComponentCategory.CAPACITORS for key in inventory):
        return
    sparse = False
    numbers: dict[tuple[str, int], set[int]] = {}
    for data in inventory.values():
        for ref in data["refs"]:
            m = _SERIES_REF.fullmatch(ref)
            if m:
                n = int(m.group(2))
                numbers.setdefault((m.group(1), n // _SERIES), set()).add(n)
    for (prefix, hundred), ns in sorted(numbers.items()):
        first = hundred * _SERIES or 1
        span = max(ns) - first + 1
        if span < _SPARSE_MIN or len(ns) >= _SPARSE_SHARE * span:
            continue
        sparse = True
        for n in range(first, max(ns)):
            if n not in ns:
                _warn(
                    stats,
                    ParseWarningKind.UNEXTRACTED_REF,
                    f"{prefix}{n}",
                    f"{prefix}{n} may be missing: the parts list numbers its "
                    f"{prefix} parts up to {prefix}{max(ns)} but lists only {len(ns)}.",
                )
    if sparse:
        _warn(
            stats,
            ParseWarningKind.IMAGE_PARTS_LIST,
            "",
            "No capacitors were read and the parts are numbered sparsely, so the "
            "parts list itself was likely not read: it may be printed as an image. "
            "Check the document.",
        )


# Words that describe a resistor or capacitor ('100n Film', '1u Tantalum').
_PASSIVE_WORDS = frozenset(
    {
        "FILM",
        "BOX",
        "POLY",
        "MLCC",
        "CERAMIC",
        "CERAMICS",
        "TANTALUM",
        "ELECTRO",
        "ELECTROLYTIC",
        "METAL",
        "CARBON",
        "BIPOLAR",
        "AXIAL",
        "RADIAL",
        "LOW",
        "ESR",
        "NP",
        "NPO",
        "C0G",
        "X7R",
        "WIMA",
        "MF",
        "MKT",
        "MKP",
        "SILVER",
        "MICA",
        "TRIM",
        "TRIMMER",
        "LOG",
        "LIN",
        "REV",
        "AUDIO",
        "LINEAR",
        "DUAL",
        "MINI",
        "SMD",
    }
)


_RUN_ON_LABEL = re.compile(r"[A-Za-z]{2,}\d{1,2}")
# Two-value rows that make a list a table of substitutes.
_PAIRED_MIN = 4
_PAIRED_SHARE = 0.5


def check_two_values(inventory: Inventory, stats: StatsDict) -> None:
    """Warns about a resistor or capacitor whose value is two values side by side.

    'R6  7k5  8k2' is a part and its substitute (GuitarPCB G2 Animal's table of
    suggested values); read as one value, it is neither. A voltage rating
    ('22n 63V') is no second value.

    Args:
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    paired: set[str] = set()
    for key, data in inventory.items():
        if key.category not in (
            ComponentCategory.RESISTORS,
            ComponentCategory.CAPACITORS,
        ):
            continue
        tokens = key.value.split()
        if len(tokens) < 2 or tokens[1][-1:].upper() in ("V", "%", "W"):
            continue
        if parse_value_to_decimal(tokens[0]) is not None and (
            is_designator(tokens[1])
            # A label from the next table: '330p  Pos1 33pF' (Tonepad Small Clone).
            or _RUN_ON_LABEL.fullmatch(tokens[1]) is not None
            # One stray word; a remark ('620R Lower Value') is several.
            or (
                len(tokens) == 2
                and tokens[1].isalpha()
                and tokens[1].istitle()
                and tokens[1].upper() not in _PASSIVE_WORDS
            )
        ):
            # 'R12  1k  P3 LEVEL 100k Log', 'R14  10k  Buffer' (older GuitarPCB Tube
            # Screaming Ultra): the value ran into the next cell.
            for ref in data["refs"]:
                _warn(
                    stats,
                    ParseWarningKind.UNPARSED_VALUE,
                    ref,
                    f"{ref} value '{key.value}' runs into other text: check its value.",
                )
            continue
        first, second = (parse_value_to_decimal(t) for t in tokens[:2])
        if first is None or second is None or first == second:
            continue
        for ref in data["refs"]:
            paired.add(ref)
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref} lists two values ('{key.value}'): check which one to use.",
            )
    refs = [ref for data in inventory.values() for ref in data["refs"]]
    if len(paired) < _PAIRED_MIN or len(paired) < _PAIRED_SHARE * len(refs):
        return
    # Most rows pair a value with a substitute: the list read is that table,
    # and its other rows are no plainer ('R36  620R  Lower Value -Higher
    # Gain', 'R43  3k3  LED CLR' in G2 Animal v3).
    warned = {w.ref for w in stats["warnings"]}
    for ref in refs:
        if ref not in warned:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref} was read from a list where most parts give two values, "
                "likely a table of substitutes: check its value in the parts list.",
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


# A footnote that says how to build without the marked parts: 'To omit this
# control, use 47n for C5 and omit CX3, CX4 and the Low Freq switch' (Aion FX Lumin).
_OMIT_INSTRUCTION = re.compile(
    r"\bto omit\b|\bleave (?:it |this |them )?off\b"
    r"|\bif you (?:do )?(?:not )?want to (?:leave|omit)\b",
    re.IGNORECASE,
)
# '1 Midrange knob: ...' may share its line with the table beside it ('R19 1k 1
# Midrange knob: ...'); a footnote without a heading starts its line.
_FOOTNOTE_LINE = re.compile(
    r"(?:^|\s)(\d{1,2}) ([A-Z][^:]{2,40}:.*)$|^(\d{1,2}) (\S.*)$"
)
_FOOTNOTE_LINES = 8
_ONLY_ONE = re.compile(r"\buse only one of (?:these|them|the two)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# 'use 39k for R15': a value change that builds the circuit another way.
_VALUE_FOR = re.compile(
    r"\buse \d[\w.]* for (?:both )?[A-Z]{1,3}\d{1,3}\b", re.IGNORECASE
)


def check_numbered_footnotes(
    marked: list[list[tuple[str, str]]],
    page_texts: list[str],
    inventory: Inventory,
    stats: StatsDict,
) -> None:
    """Warns about listed parts whose footnote says how to build without them.

    Args:
        marked: Per page, the (row label, marker) pairs of footnoted values.
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    listed = {ref.upper(): ref for data in inventory.values() for ref in data["refs"]}
    for rows, text in zip(marked, page_texts, strict=True):
        if not rows:
            continue
        lines = [" ".join(line.split()) for line in (text or "").splitlines()]
        notes: dict[str, str] = {}
        for k, line in enumerate(lines):
            head = _FOOTNOTE_LINE.search(line)
            if not head:
                continue
            mark, first = head.group(1) or head.group(3), head.group(2) or head.group(4)
            body = [first]
            for more in lines[k + 1 : k + _FOOTNOTE_LINES]:
                if _FOOTNOTE_LINE.search(more):
                    break
                body.append(more)
            notes.setdefault(mark, " ".join(body))

        def named(label: str, note: str) -> bool:
            return (
                re.search(rf"\b{re.escape(label)}\b", note, re.IGNORECASE) is not None
            )

        # 'Use only one of these' (Aion FX legacy Aphelion: IC1 single or IC2
        # dual op-amp): the marked parts are alternatives, not all bought.
        for label, mark in rows:
            ref = listed.get(label.upper())
            if ref and _ONLY_ONE.search(notes.get(mark, "")):
                _warn(
                    stats,
                    ParseWarningKind.FOOTNOTE,
                    ref,
                    f"{ref} has a note: {notes[mark]} Buy only the one you use.",
                )

        # Only a page whose footnotes rebuild the circuit another way ('use 39k
        # for R15') describes another build; leaving off optional parts alone
        # ('leave off IC2, D2-D3, and C11', Aion FX legacy Crescent) does not.
        if not any(
            _OMIT_INSTRUCTION.search(n) and _VALUE_FOR.search(n) for n in notes.values()
        ):
            continue
        for label, mark in rows:
            note = notes.get(mark, "")
            ref = listed.get(label.upper())
            # The omit instruction and the footnote's heading say which parts
            # it concerns: 'High frequency switch: ... I recommend socketing C4,
            # CX1 and CX2 ... To omit this control, just leave off CX1, CX2 and
            # the High Freq switch' (not C4, though marked too).
            heading = note.split(":", 1)[0] if ":" in note else ""
            scope = " ".join(
                [
                    heading,
                    *(t for t in _SENTENCE.split(note) if _OMIT_INSTRUCTION.search(t)),
                ]
            )
            names_any = any(named(other, scope) for other, m in rows if m == mark)
            if names_any and not named(label, scope):
                continue
            if ref and _OMIT_INSTRUCTION.search(note):
                _warn(
                    stats,
                    ParseWarningKind.FOOTNOTE,
                    ref,
                    f"{ref} has a note on building without it: {note}",
                )


# A build-notes passage that builds another version: 'Building the RC Booster',
# 'Bass variant', 'TS-25 variant (higher gain)', 'If you'd like to build the post-1994 version of the DS-1, make
# the following substitutions'. Not a modification ('Color switch modification').
_BUILD_PASSAGE = re.compile(
    r"^building (?:the |an? )?\S|^\S+(?: \S+){0,3} (?:variant|version)(?: \([^()]*\))?$"
    r"|\bbuild (?:the|an?) [^.]*\b(?:variant|version)\b"
    # 'The Analogman version of the Astrotone uses 1M2 for R1 and R4' (Aion FX
    # legacy Astra footnote).
    r"|\bversion\b[^.]*\buses \S*\d\S* for "
    # '...if you do want to omit it, use 10k for R24 and then jumper all three pads
    # of the Tone pot together' (Aion FX Fractal): building without a control.
    r"|\bomit (?:it|them)\b[^.]*\buse \S*\d\S* for "
    # 'The Bearfoot version changes the Nature control so it's more useful. Use
    # 220n for C5 and 4k7 for RX1.' (Aion FX legacy Procyon)
    r"|\b[A-Z]\w+ version\b[^.]*\.\s+Use \S*\d\S* for "
    # 'If using the Body control, omit C1 and use CX2 and CX3.' (Aion FX legacy
    # Solaris)
    r"|\bif using the \w+ (?:control|pot), omit\b"
    # 'if you do want to build the output section to Ross/Dyna specs, you can use a
    # 47n film capacitor for CX1 and omit R22-24 and Q6' (Aion FX Aurora).
    r"|\b(?:stock|specs?|version|circuit)\b[^.]*?, you can use\b"
    # 'If you want triangle-wave mode instead, here are the changes to make' (Aion FX Argus).
    r"|\bhere are the changes to make\b|\bmake the following (?:changes|substitutions)\b"
    # 'If you'd like to build the original Runoffgroove circuit, which runs at 9V,
    # the following values need to be changed.' (Aion FX Supreaux)
    r"|\bthe following values need to be changed\b"
    # 'Start with the Dyna Comp values above, and make the following
    # replacements:' (Aion FX legacy Aurora's JangleBox).
    r"|\bmake the following replacements\b"
    # '808 Style Build: Use Landgraff Dynamic Overdrive values with these exceptions
    # C4: 47n, ...', '...JUMPER D2 and change values of R4 to 1M, R5 to 470K'
    # (GuitarPCB Tube Screaming Ultra).
    r"|\bstyle build\b|\bchange values? of\b"
    # 'Bypassing Q4' (Aion FX Halo v1): a heading for building without a stage.
    r"|^bypassing [A-Z]{1,3}\d{1,3}$",
    re.IGNORECASE,
)
_MODIFICATION = re.compile(r"\bmod(?:s|ification|ifications)?\b", re.IGNORECASE)
# A passage ends at the next heading: a short line with no closing punctuation.
_NOTE_HEADING = re.compile(r"^[A-Z][\w'\u2019&/ -]{2,40}$")
_PASSAGE_LINES = 25
_DESIGNATOR = r"[A-Z]{1,3}\d{1,3}(?:-\d{1,3})?"
# '• C5: 470n → 68n', '• R6: jumper', '• D2-8: omit (leave empty)'.
_CHANGE_ITEM = re.compile(rf"^[•*-]?\s*({_DESIGNATOR})\s*:\s*\S")
# '• Fuzz: 250kB' (Aion FX Phobos): a control, named.
_CONTROL_ITEM = re.compile(r"^[•*-]\s*([A-Z][A-Za-z]+)\s*:\s*\d")
# A lead-in sentence opens a passage of changes, as a colon does.
_LEAD_IN = re.compile(
    r"\bthe following (?:changes|substitutions|replacements|values)\b", re.IGNORECASE
)
# 'Use 22n for C12 instead of 27n', 'Use 16k for R36 and 14k for R37', 'Jumper R8',
# '470R for R6, and 470n for C1 and C4'.
_CHANGE_PROSE = re.compile(
    rf"\b(?:use (?:an? \S*\d\S*(?: \w+){{0,2}}|\S+)|\S*\d\S*) for ({_DESIGNATOR})"
    rf"(?: and ({_DESIGNATOR}))?"
    rf"(?: and \S+ for ({_DESIGNATOR}))?"
    # 'omit R20-23, Q4, and C13' (Aion FX Halo v1): a list.
    rf"|\b(?:jumper|omit|leave off) ({_DESIGNATOR}(?:(?:,\s*|,?\s+and\s+){_DESIGNATOR})*)\b"
    # 'RX1/RX2: 1k → 500R; Sustain: 500kC → 250kB' (Aion FX legacy Aurora).
    rf"|\b({_DESIGNATOR})/({_DESIGNATOR}):"
    r"|\b([A-Z][a-z]+): \S+ \u2192"
    # 'exceptions C4: 47n, Gain Pot: B500k, R6: 51k', 'R4 to 1M, R5 to 470K',
    # 'D2 is replaced by a jumper'.
    rf"|\b({_DESIGNATOR}):\s*\d|\b({_DESIGNATOR}) to \d|\b({_DESIGNATOR}) is replaced\b",
    re.IGNORECASE,
)
# A control left out: 'jumper all three pads of the Tone pot together' (Aion FX
# Fractal), whose designator is its name.
_OMIT_CONTROL = re.compile(
    r"\b(?:jumper|omit)\b[^.]*?\bthe (\w+) (?:pot|control)\b", re.IGNORECASE
)
# 'The part list aside from IC1 and D4 is identical in both versions' (GuitarPCB MoRC).
_SAME_EXCEPT = re.compile(
    rf"\b(?:aside|apart) from ((?:{_DESIGNATOR})(?:(?:,\s*|\s+and\s+)(?:{_DESIGNATOR}))*)"
    r" (?:is|are) (?:identical|the same) in (?:both|all) versions\b",
    re.IGNORECASE,
)
# 'do not install R1, R2 R3, C1 and Q1' (Tube Screaming Ultra's Robin Trower build).
_OMIT_LIST = re.compile(
    rf"\bdo not install ((?:{_DESIGNATOR})(?:(?:,\s*|\s+and\s+|\s+)(?:{_DESIGNATOR}))*)",
    re.IGNORECASE,
)


def _designators(label: str) -> list[str]:
    """'D2-8' -> D2..D8; anything else is one designator."""
    m = re.fullmatch(r"([A-Z]+)(\d+)-(\d+)", label)
    if not m:
        return [label]
    lo, hi = int(m.group(2)), int(m.group(3))
    return (
        [f"{m.group(1)}{n}" for n in range(lo, hi + 1)]
        if lo < hi <= lo + 20
        else [label]
    )


def check_build_changes(
    page_texts: list[str],
    inventory: Inventory,
    stats: StatsDict,
    omitted: frozenset[str] = frozenset(),
) -> None:
    """Warns about listed parts a build-notes passage changes for another version.

    Aion FX Quartz lists one parts table and gives the bass version as a change
    list ('Bass variant: • R34: 1k5 → 100R'); Aurum gives the original OD-2 in
    prose ('Building the original OD-2: Use 22n for C12 instead of 27n'). The
    listed values build one version; the warned parts differ in the other.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
        omitted: Designators the list leaves empty, which another version may
            fill ('CX1 | OMIT', Aion FX Aurora: 47n for the Ross/Dyna output).
    """
    listed = {ref.upper(): ref for data in inventory.values() for ref in data["refs"]}
    listed |= {ref.upper(): ref for ref in omitted}
    warned: set[str] = set()
    for text in page_texts:
        for m in _SAME_EXCEPT.finditer(" ".join((text or "").split())):
            for label in re.findall(_DESIGNATOR, m.group(1), re.IGNORECASE):
                ref = listed.get(label.upper())
                if ref:
                    _warn(
                        stats,
                        ParseWarningKind.FOOTNOTE,
                        ref,
                        f"{ref} differs between the versions this list covers "
                        f"('{m.group(0)}'); check which your build needs.",
                    )
        lines = [" ".join(line.split()) for line in (text or "").splitlines()]
        k = 0
        while k < len(lines):
            line = lines[k]
            k += 1
            if not _BUILD_PASSAGE.search(line) or _MODIFICATION.search(line):
                continue
            # A heading or a lead-in ('...the following substitutions:') opens a
            # passage; a sentence ('The Park uses 2n2 for C6.') is its own scope.
            sentence = not (
                _NOTE_HEADING.match(line) or line.endswith(":") or _LEAD_IN.search(line)
            )
            passage = lines[k - 1 : k + _PASSAGE_LINES]
            for n, body in enumerate(passage):
                # A heading ends the passage, unless it names one of several
                # builds whose changes follow ('Vox Tone Bender Mk III', then
                # '• R1: 100k'; Aion FX Phobos).
                follows = passage[n + 1] if n + 1 < len(passage) else ""
                if (
                    n
                    and _NOTE_HEADING.match(body)
                    and not _CHANGE_ITEM.match(body)
                    and (
                        not (
                            _CHANGE_ITEM.match(follows) or _CONTROL_ITEM.match(follows)
                        )
                        or _MODIFICATION.search(body)
                    )
                ):
                    break
                if n and sentence and lines[k - 2 + n].rstrip().endswith("."):
                    break
                labels = [m.group(1) for m in [_CHANGE_ITEM.match(body)] if m]
                labels += [m.group(1) for m in [_CONTROL_ITEM.match(body)] if m]
                # A sentence wraps: 'use 470pF for' / 'CX2 and 1n for CX3'.
                window = f"{body} {follows}" if n + 1 < len(passage) else body
                for m in _CHANGE_PROSE.finditer(window):
                    for g in filter(None, m.groups()):
                        # A list of designators, or one control's name.
                        labels += re.findall(_DESIGNATOR, g, re.IGNORECASE) or [g]
                labels += [m.group(1) for m in _OMIT_CONTROL.finditer(body)]
                for m in _OMIT_LIST.finditer(body):
                    labels += re.findall(_DESIGNATOR, m.group(1), re.IGNORECASE)
                for label in dict.fromkeys(labels):
                    for name in _designators(label.upper()):
                        ref = listed.get(name)
                        if ref and ref not in warned:
                            warned.add(ref)
                            _warn(
                                stats,
                                ParseWarningKind.FOOTNOTE,
                                ref,
                                f"{ref}: the build notes change it for another "
                                f"version ('{line}'); check which your build needs.",
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
    head, _, remark = new_value.partition(" ")
    if (
        remark
        and not any(ch.isdigit() for ch in remark)
        and parse_value_to_decimal(head) is not None
        and _comparable(head) == _comparable(first_value)
    ):
        # '3k3 LED CLR' (GuitarPCB G-02 Animal's suggestion table): the same
        # value with a remark.
        return
    if not any(ch.isdigit() for ch in new_value) and first_value.upper().endswith(
        f" {new_value.upper()}"
    ):
        # 'LED' is the second line of a wrapped 'CA Bi-colour / LED' cell, read
        # alone by another reader (GuitarPCB Dr Phybes).
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
    # 'Stock Ross/Dyna uses 500kB' (Aion FX Aurora) is the original unit too.
    r"(?!(?i:the )?(?:(?i:original|stock|older|some|most|many|factory|can|could|may|you)\b"
    r"|(?i:early|earlier|later)\b(?! [\w-]*\d)))"
)
# A build named as a sentence's subject: capitalized words or model numbers
# ('Bass RC', 'RC Booster and Bass RC'), not a phrase ('Leave empty or omit this').
_SUBJECT_WORD = r"[A-Z0-9][\w/-]*"
_SUBJECT = rf"{_HISTORY}{_SUBJECT_WORD}(?: {_SUBJECT_WORD}){{0,3}}"
# A build's name: words without a sentence-ending period ('TS-9', 'Tube Sound Fuzz').
_NAME_WORD = r"(?:[\w/-]|\.(?=\w))+"
# A build named before its value: 'Formula 5F6', 'DLS Red'. Not a generic label
# ('Alternate: 4K7', 'Sub: MPSA18', 'Quantity: 2', 'Note: ...').
_BUILD_LABEL = (
    r"(?!(?i:alt(?:ernat(?:e|ive))?|sub(?:stitute)?|quantity|qty|notes?|use|or|"
    r"optional|recommended|see|pin|ratio|max|min|value|rating)\b)"
    r"[A-Z][\w&/'-]*(?: [\w&/'-]+){0,2}"
    # 'Galaxie mod: omit' (Aion FX Sapphire) names a modification, not a build.
    r"(?<!(?i: mod))(?<!(?i: mods))"
)
_VERSION_NOTE = re.compile(
    rf"\b(?i:in|for) (?:the )?{_HISTORY}(?!(?i:this|stock|standard|any|all|every)\b)"
    # '1k2 in magazine project' (Aion FX Wyvern).
    r"[\w' /.-]+? (?i:version|variant|specs?|circuit|project)\b"
    # 'Use a 100n film capacitor here if building a 3-transistor variant'.
    r"|\b(?i:if building)\b"
    # 'Analogman version uses 1.2M here', 'GC-1 uses 150k', 'G-2 uses 22uF'.
    # 'The HM-2 uses 2SK30A-Y' (Aion FX Graviton) names the original unit: history.
    rf"|(?:^|[.;]\s*)(?!The ){_HISTORY}(?:[A-Z]{_NAME_WORD}?(?: {_NAME_WORD}){{0,3}}) uses \S"
    # 'RC Booster and Bass RC use 22k', 'Bass RC omits this diode', 'RC Booster
    # and Bass RC jumper this' (Aion FX Malacandra).
    rf"|(?:^|[.;]\s*){_SUBJECT}(?: and {_SUBJECT})? (?:use \S|(?:omit|jumper)s? (?:this|it)\b)"
    # 'Use 16k for original OD-2' (Aion FX Aurum): a model number, not a part
    # ('Use 100n for C4').
    rf"|\b(?i:use) \d\S* (?:in|for) (?:the )?(?i:original )?(?![A-Z]{{1,3}}\d+\b)[A-Z][\w-]*\d"
    # 'Use 220k for stock version' (Aion FX Particle): another version's value,
    # though 'for stock version' alone restates the list.
    r"|\b(?i:use) \d\S* (?:in|for) (?:the )?\w+ (?i:version|variant|build)\b"
    # 'Omit (leave empty) for P-2', 'Jumper for GC-1', 'Omit if using Morning Glory'.
    rf"|\b(?i:omit|jumper)\b[^.;]*?\b(?i:for|in|if using) {_HISTORY}(?:the )?[A-Z]"
    # '100pF in the SD-9M', '10M in Catalinbread and Orpheum'.
    rf"|\bin the {_HISTORY}[A-Z][\w-]*\d"
    # '470pF MLCC for Aqua Puss' (Aion FX Amethyst): a value and its construction.
    rf"|^\S*\d\S*(?: [A-Za-z]+)? (?:in|for) {_HISTORY}[A-Z]"
    # 'Orpheum: 10M; Axis/Foxey: omit (leave empty)'. Not a generic label:
    # 'Alternate: 4K7', 'Sub: MPSA18', 'Quantity: 2' (PedalPCB) name no build.
    r"|^(?!(?i:alt(?:ernat(?:e|ive))?|sub(?:stitute)?|quantity|qty|note|notes|"
    r"use|or|optional|alternate part|recommended|see)\s*:)"
    # 'Galaxie mod: 10k' (Aion FX Sapphire) is a modification, not a build.
    r"[A-Z][\w &/'-]{1,30}(?<!(?i: mod))(?<!(?i: mods)): [\w.]+(?:\s*[;.]|$)"
    # One position, two parts: 'CX1 | 1uF | Alternative for C7', 'C7 | 4.7uF | Can
    # instead use CX1 for a 1uF film cap' (Aion FX Helios).
    r"|\b(?i:alternative (?:for|to)|can instead use) [A-Z]{1,3}X?\d"
    # 'Part of the optional Morning Glory gain stage.'
    r"|\b(?i:part of the optional)\b"
    # 'Carbon comp in original, measured value 560R Formula 5F6: 1k8 DLS Red: 1k'
    # (Aion FX Triton): a build's value anywhere in the note.
    # 'V2: J201 (MMBFJ201)' (Aion FX Muramasa): a part number.
    rf"|(?<![\w:]){_BUILD_LABEL}: (?:\(?(?i:omit)\)?|(?:[A-Z]{{1,4}}-?)?\d[\w.]*)"
    # 'Omit if using tone pot' (Aion FX Rift): one control or the other.
    r"|\b(?i:omit if using (?:an? |the )?\w+ (?:pot|potentiometer|switch|toggle))\b",
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


# A version named by the part it is built around: 'Use 1k for MN3207 version'.
_PART_VERSION = re.compile(
    r"\b(?:for|if building)(?: the| an?)? ([A-Z]{2,}\d{3,}[A-Z]*) version\b",
    re.IGNORECASE,
)


def check_part_versions(stats: StatsDict, rows: list[tuple[str, str, str]]) -> None:
    """Warns about the listed part a part-numbered version swaps out.

    Aion FX Lithium lists IC2 as MN3007, and its NOTES give values 'for MN3207
    version': that version replaces IC2 itself, though IC2's own row says nothing.

    Args:
        stats: Stats object that receives the warnings.
        rows: The document's (designator, value, notes) rows.
    """
    names = {m.group(1).upper() for _, _, n in rows for m in _PART_VERSION.finditer(n)}
    for ref, value, _ in rows:
        listed = value.upper()
        for name in names:
            near = len(listed) == len(name) and listed[:2] == name[:2]
            if near and sum(a != b for a, b in zip(listed, name, strict=True)) == 1:
                _warn(
                    stats,
                    ParseWarningKind.FOOTNOTE,
                    ref,
                    f"{ref}: listed as {value}, but the document describes an "
                    f"{name} version; check which your build needs.",
                )


def check_alternate_value(stats: StatsDict, ref: str, listed: str, other: str) -> None:
    """Warns about a listed part another version of the circuit gives another value.

    Aion FX legacy Refractor's 'Silver Pony specs' table: 'R10 | 2k | 47R'.

    Args:
        stats: Stats object that receives the warning.
        ref: The designator.
        listed: The value the parts list gives.
        other: The other version's value.
    """
    _warn(
        stats,
        ParseWarningKind.FOOTNOTE,
        ref,
        f"{ref}: listed as {listed}, but the document gives {other} for another "
        "version of the circuit; check which your build needs.",
    )


# A note that says what to buy: a construction or a value ('Mod value is MLCC,
# not film'), unlike 'See build notes' or 'Adjust value to change brightness'.
_PART_DETAIL = re.compile(
    r"\b(?:MLCC|film|tantalum|electrolytic|ceramic|bipolar)\b"
    r"|\b\d+(?:\.\d+)?\s?(?:[pnuµ]F?|[kKMR]\d*)\b",
    re.IGNORECASE,
)


def check_build_note(stats: StatsDict, ref: str, builds: list[str], notes: str) -> None:
    """Warns about a note on a row whose builds take different values.

    Aion FX Elite's C13 is '1n | 1n | 820pF' with the note 'Mod value is MLCC,
    not film': the note concerns one build, and which one cannot be read.

    Args:
        stats: Stats object that receives the warning.
        ref: The row's designator.
        builds: The row's value in each build.
        notes: The row's NOTES cell.
    """
    note = " ".join(notes.replace("*", " ").split())
    if (
        len(set(builds)) > 1
        and _PART_DETAIL.search(note)
        and not _VERSION_NOTE.search(note)
    ):
        _warn(
            stats,
            ParseWarningKind.FOOTNOTE,
            ref,
            f"{ref}: '{note}'. The builds differ here and the note may apply to "
            "only one of them; check this part for your build.",
        )


# 'R1 = 100k' on a line of its own: a build note's list of changes for another
# version (Aion FX legacy Rift: the Shin-Ei FY-2 changes R1, C2, R9, R19).
_ASSIGNMENT = re.compile(r"\s*([A-Z]{1,4}\d{1,3})\s*=\s*(\S+(?: \S+)?)\s*")
_ASSIGNMENTS_MIN = 2


def check_value_assignments(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about listed parts a list of 'REF = value' changes gives another value.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    listed = {
        ref.upper(): key.value
        for key, data in inventory.items()
        for ref in data["refs"]
    }
    for text in page_texts:
        found = [m for line in text.splitlines() if (m := _ASSIGNMENT.fullmatch(line))]
        if len(found) < _ASSIGNMENTS_MIN:
            continue
        for m in found:
            ref, value = m.group(1), m.group(2)
            have = listed.get(ref.upper())
            if have is None or _same_value(have, value):
                continue
            _warn(
                stats,
                ParseWarningKind.FOOTNOTE,
                ref,
                f"{ref}: the document's notes give '{ref} = {value}' (this list has "
                f"{have}), perhaps for another version. Check which your build needs.",
            )


def _same_value(a: str, b: str) -> bool:
    da, db = parse_value_to_decimal(a.split()[0]), parse_value_to_decimal(b.split()[0])
    return da == db if da is not None and db is not None else a.upper() == b.upper()


# Pots given in prose, not in the parts list: 'the Sustain and Tone pots are
# 100kB and Volume is 100kA' (Aion FX legacy Halo).
_PROSE_POTS = re.compile(
    r"\b(?P<names>[A-Z][a-z]+(?:(?:, | and )[A-Z][a-z]+)*)"
    r"(?: (?:pots?|controls?))? (?:are|is) (?P<value>\d+(?:\.\d+)?[kKM][ABCW])\b"
)


def check_prose_pots(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about pots a document names only in its prose.

    Only when the parts list gave no pots at all: then a sentence naming
    controls and a taper value is where they are.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    if any(key.category == ComponentCategory.POTENTIOMETERS for key in inventory):
        return
    text = " ".join(" ".join(page_texts).split())
    for m in _PROSE_POTS.finditer(text):
        for name in re.split(r", | and ", m.group("names")):
            ref = name.upper()
            _warn(
                stats,
                ParseWarningKind.UNEXTRACTED_REF,
                ref,
                f"{ref} ({m.group('value')}) is given in the document's notes, not its "
                "parts list: add it by hand.",
            )


_CHIP = r"[A-Z]{2,}\d{3,}[A-Z]*"
# 'either the MN3005/MN3101 or the MN3205/MN3102 chipset' (Aion FX Amethyst): two
# sets of chips, each part of one set taking the place of the other's.
_CHIP_SETS = re.compile(
    rf"\b({_CHIP})/\s?({_CHIP}) or (?:the )?({_CHIP})/\s?({_CHIP})\b"
)


def check_chip_sets(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about a listed chip the document offers a whole other set for.

    Aion FX Amethyst lists the MN3005 and its MN3101 clock and says the board
    takes 'either the MN3005/MN3101 or the MN3205/MN3102 chipset': IC4 is an
    MN3102 in the other build, though its row says only 'See build notes'.

    Args:
        page_texts: Raw text of every page.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    text = " ".join(" ".join(page_texts).split())
    other: dict[str, str] = {}
    for m in _CHIP_SETS.finditer(text):
        first, second = m.groups()[:2], m.groups()[2:]
        for a, b in zip(first, second, strict=True):
            other.setdefault(a, b)
            other.setdefault(b, a)
    for key, data in inventory.items():
        swap = other.get(key.value.upper())
        for ref in data["refs"] if swap else []:
            _warn(
                stats,
                ParseWarningKind.FOOTNOTE,
                ref,
                f"{ref}: listed as {key.value}, but the document offers a set of "
                f"chips with {swap} in its place; check which your build needs.",
            )


def check_unreadable_rows(rows: list[str], stats: StatsDict) -> None:
    """Warns that a row of an OCR'd parts list names no part it could read.

    Args:
        rows: The rows' text (`layout.unreadable_rows`).
        stats: Stats object that receives the warning.
    """
    if rows:
        listed = "', '".join(rows)
        _warn(
            stats,
            ParseWarningKind.UNREADABLE_ROW,
            "",
            f"The designator of '{listed}' could not be read, so that part is "
            "missing from the list: check the document and add it by hand.",
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


# A capacitor value or the word itself: Aion FX legacy Stratus's SD-1 table puts
# '18n capacitor' in the R8 position.
_CAPACITANCE = re.compile(r"^\d+(?:\.\d+)?[pnuµ]\d?F?\b|\bcapacitor\b", re.IGNORECASE)


# A value that only points to the build notes: 'See Text' (GuitarPCB G-02 Animal).
# 'See Text', or a part type then the pointer: 'PNP—See Text' (GuitarPCB PUFF v4).
_SEE_TEXT_VALUE = re.compile(
    r"(?:.*[-\u2013\u2014]\s*)?\s*see (?:build )?(?:notes?|text)\s*", re.IGNORECASE
)


def check_pointer_value(stats: StatsDict, ref: str, raw_value: str) -> None:
    """Warns when a part's value only points to the build notes.

    'Q1-Q6  See Text' (GuitarPCB G-02 Animal) names no part the classifier can
    read, so the part would otherwise vanish without a trace.

    Args:
        stats: Stats object that receives the warnings.
        ref: The designator.
        raw_value: The value exactly as extracted.
    """
    if _SEE_TEXT_VALUE.fullmatch(raw_value):
        _warn(
            stats,
            ParseWarningKind.UNPARSED_VALUE,
            ref,
            f"{ref} value is '{raw_value.strip()}': the document gives it in its "
            "text; check the build notes for what to use.",
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
        # An LED's current-limiting resistor named after it (Aion FX: 'LED1  10k
        # Metal film resistor'); the classifier only says resistor on a TYPE hint.
        led_resistor = prefix == "LED" and category == ComponentCategory.RESISTORS
        if (
            expected is not None
            and category != expected
            and not (vactrol or trimmer or led_resistor)
        ):
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

    if category == ComponentCategory.RESISTORS and _CAPACITANCE.search(raw_value):
        _warn(
            stats,
            ParseWarningKind.CATEGORY_CONFLICT,
            ref,
            f"{ref} ('{raw_value}') is listed under a resistor designator, but "
            "its value is a capacitor's.",
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
        first, second = (g for g in pair.groups() if g)
        if stats is not None:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref}: the document allows {first} or {second}; {first} is used.",
            )
        return first
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
        return either.group(1) + either.group(3)
    examples = _EXAMPLES.search(value)
    if examples:
        chosen = value[: examples.start()].strip()
        if stats is not None:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref} is a type, not a part number ('{chosen}'); the document "
                f"suggests {examples.group(1)}.",
            )
        return chosen
    alternative = _OR_ALTERNATIVE.search(value)
    if alternative:
        chosen = " ".join(_OR_ALTERNATIVE.sub(" ", value).split())
        if stats is not None:
            _warn(
                stats,
                ParseWarningKind.VALUE_CHOICE,
                ref,
                f"{ref}: the document also allows "
                f"{alternative.group(1) or alternative.group(2)}; "
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


def check_misread_text(stats: StatsDict, page: int) -> None:
    """Tells the builder a page's text was set aside as misread OCR text.

    Args:
        stats: Stats object that receives the warning.
        page: The page, counting from 0.
    """
    _warn(
        stats,
        ParseWarningKind.MISREAD_TEXT,
        "",
        f"The text on page {page + 1} was recognized from an image by another "
        "program and is full of misreads ('lOK' for '10K', 'Rl' for 'R1'), so it "
        "was not used.",
    )


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


# A parts table keyed by value, with no designator column: 'PART | QTY | TYPE |
# NOTES' where PART is '1N5817 (D1)' or '100n (0.1uF)' (Aion FX 3PDT Bypass).
_VALUE_KEYED_HEADER = re.compile(
    r"^\s*PART\s+QTY\s+TYPE\b(?!.*\bVALUE\b)", re.MULTILINE
)


def check_value_keyed_list(page_texts: list[str], stats: StatsDict) -> None:
    """Tells the builder when the parts list names parts by value, not designator.

    Whatever was read elsewhere in the document (a schematic's labels) is not
    that list, so nothing extracted can be trusted as complete.

    Args:
        page_texts: Raw text of every page.
        stats: Stats object that receives the warning.
    """
    if any(_VALUE_KEYED_HEADER.search(text) for text in page_texts):
        _warn(
            stats,
            ParseWarningKind.NO_PARTS_LIST,
            "",
            "The parts list names its parts by value, not by designator ('PART | QTY | "
            "TYPE'), so it could not be read. Enter the parts by hand.",
        )


# A parts list row that counts parts instead of naming them: '4 - 10k',
# '2 - 0.082 µF' (Tonepad). The count is one or two digits; the value starts
# with a digit and carries a multiplier or unit, and the dash is spaced:
# '3-4' and '10-30k' are ranges.
_QUANTITY_ROW = re.compile(
    r"(?<![\w.\-])(\d{1,2})(?:\s+[-\u2013]\s*|[-\u2013]\s+)"
    r"(\d[\d.,]*(?:[kKMR]\d*|\s?[pnuµμ]F|[pnuµμ]\d*))(?![\w.\-])"
)
_QUANTITY_MIN = 2


def check_quantity_list(page_texts: list[str], stats: StatsDict) -> None:
    """Tells the builder about parts counted by value instead of listed by designator.

    Tonepad lists '4 - 10k' under a Resistors heading: four 10k resistors, no
    designators. Those parts cannot be read (the parts list is keyed by
    designator), so the document as a whole is warned. Parts the same list
    does name ('IC1 - PT2399') are still read.

    Args:
        page_texts: Raw text of every page.
        stats: Stats object that receives the warning.
    """
    rows = [
        m.group(0)
        for text in page_texts
        for m in _QUANTITY_ROW.finditer(text)
        if parse_value_to_decimal(m.group(2).replace(" ", "")) is not None
    ]
    if len(rows) < _QUANTITY_MIN:
        return
    _warn(
        stats,
        ParseWarningKind.QUANTITY_LIST,
        "",
        f"{len(rows)} lines of the parts list count parts by value ('{rows[0]}') "
        "instead of naming them by designator, so those parts were not read. Enter "
        "them by hand.",
    )


def check_pictured_lists(
    pictured: dict[int, list[str]], refs_by_page: dict[int, set[str]], stats: StatsDict
) -> None:
    """Warns about parts lists printed as pictures, which the text layer never reads.

    The list's parts are missing, so the document as a whole is warned. A
    numbered designator read from another list on the same page is warned
    too: the pictured list numbers its own parts and may use the same
    designators (GuitarPCB Sonic Architect: both boards have an R1), so which
    board a part belongs to is uncertain. Control names (BASS, GAIN) are each
    board's own.

    Args:
        pictured: Headings of pictured lists, by page index.
        refs_by_page: Designators read from each page's text.
        stats: Stats object that receives the warnings.
    """
    for page, headings in sorted(pictured.items()):
        names = ", ".join(f"'{h}'" for h in headings)
        _warn(
            stats,
            ParseWarningKind.IMAGE_PARTS_LIST,
            "",
            f"The parts list {names} on page {page + 1} is an image, so its parts "
            "were not read. Check the document.",
        )
        for ref in sorted(refs_by_page.get(page, ())):
            if not _SERIES_REF.fullmatch(ref):
                continue
            _warn(
                stats,
                ParseWarningKind.IMAGE_PARTS_LIST,
                ref,
                f"{ref} was read beside the image parts list {names} on page "
                f"{page + 1}, which may use the same designators for another "
                "board. Check which list it belongs to.",
            )


def _same_listing(a: str, b: str) -> bool:
    """Whether two printed values name one part: '1k8'/'1.8k', 'SPST Toggle'/'SPST'."""
    x, y = (" ".join(v.upper().split()) for v in (a, b))
    if x == y or x.startswith(y + " ") or y.startswith(x + " "):
        return True
    # Numbers compare before upper-casing: the micro sign upper-cases to Greek Mu.
    head = [v.split()[0] if v.split() else "" for v in (a, b)]
    dx, dy = (parse_value_to_decimal(h) for h in head)
    return dx is not None and dx == dy and x.split()[1:] == y.split()[1:]


_OTHER_LIST_SHARE = 0.6
# A cell of a versions table: a value, or an empty spot ('(omit)', 'Empty',
# '_' in GuitarPCB Emexar v4).
_GRID_CELL = re.compile(
    r"(?<!\S)(?:\d+(?:\.\d+)?[pnuµkKMR]?\d*[A-Za-z]{0,2}"
    r"|\(?(?:omit|jumper|empty|nothing)\)?|_)(?!\S)",
    re.IGNORECASE,
)
_GRID_SHARE = 0.25


def check_other_list(
    page: int, listed: list[tuple[str, str]], read: dict[str, str], stats: StatsDict
) -> None:
    """Warns about a second parts list on a page the readers left alone.

    The readers keep to the pages of a document's parts list, so a list
    elsewhere is never read: GuitarPCB Stage 3's page 5 lists the previous
    board version ('R1 | *Text', 'C2 | 22n') under no table header. Each
    designator it gives another value is warned, as the part another version
    or board would need.

    Args:
        page: The page index of the unread list.
        listed: The (designator, value) pairs it prints.
        read: The value read for each designator from the parts list.
        stats: Stats object that receives the warnings.
    """
    several = sum(len(_GRID_CELL.findall(value)) >= 2 for _, value in listed)
    if several >= _GRID_SHARE * len(listed):
        # A versions table, one column per build ('DEPTH | 350kB 1MB 100kB',
        # Aion FX Orpheus); the document's version notes cover it.
        return
    _warn(
        stats,
        ParseWarningKind.CONFLICTING_DUPLICATE,
        "",
        f"Page {page + 1} has another parts list, which was not read: it may "
        "describe another version or board. Check the document.",
    )
    for ref, value in listed:
        if ref in read and not _same_listing(value, read[ref]):
            _warn(
                stats,
                ParseWarningKind.CONFLICTING_DUPLICATE,
                ref,
                f"{ref} is {value!r} in another parts list on page {page + 1}, which "
                f"was not read; {read[ref]!r} is used. Check the document.",
            )
    # A whole other list (most designators in common) that lacks a part
    # describes a build without it: Stage 3's previous board has no R6 or D2.
    numbered = {r for r in read if _SERIES_REF.fullmatch(r)}
    others = {ref for ref, _ in listed}
    if numbered and len(numbered & others) >= _OTHER_LIST_SHARE * len(numbered):
        for ref in sorted(numbered - others):
            _warn(
                stats,
                ParseWarningKind.CONFLICTING_DUPLICATE,
                ref,
                f"{ref} is missing from another parts list on page {page + 1}, which "
                "was not read: another version or board may not use it. Check the "
                "document.",
            )


# 'TR1 / R18 - either the trim pot or fixed resistor is used but not both'
# (GuitarPCB Super Drive 70s).
# 'Install R9 or TR1 but do not install both' (GuitarPCB Sriracha Fuzz).
_EITHER_PART = re.compile(
    r"\b([A-Z]{1,4}\d{1,3})\s*(?:/|\bor\b)\s*([A-Z]{1,4}\d{1,3})\b[^.]{0,40}?"
    r"(?:\beither\b[^.]*)?\bnot (?:install |use )?both\b",
    re.IGNORECASE,
)
# 'only install T2 and not the Boost potentiometer' (GuitarPCB ColorTone Bass
# Fuzz): a trimmer or a pot in one place.
_ONLY_INSTALL = re.compile(
    r"\bonly install (?:the )?(\w+)(?: potentiometer| pot| trimmer| trim ?pot)? and "
    r"(?:do )?not (?:install )?(?:the )?(\w+)",
    re.IGNORECASE,
)


def check_either_part(
    page_texts: list[str], inventory: Inventory, stats: StatsDict
) -> None:
    """Warns about two listed parts the document says are alternatives.

    Args:
        page_texts: Every page's text.
        inventory: The finished inventory for this document.
        stats: Stats object that receives the warnings.
    """
    listed = {ref.upper() for data in inventory.values() for ref in data["refs"]}
    warned: set[frozenset[str]] = set()
    for text in page_texts:
        flat = " ".join(text.split())
        for m in [*_EITHER_PART.finditer(flat), *_ONLY_INSTALL.finditer(flat)]:
            a, b = m.group(1).upper(), m.group(2).upper()
            # The notes often say it twice, once for each choice.
            if a == b or frozenset((a, b)) in warned:
                continue
            warned.add(frozenset((a, b)))
            if a in listed and b in listed:
                for ref, other in ((a, b), (b, a)):
                    _warn(
                        stats,
                        ParseWarningKind.VALUE_CHOICE,
                        ref,
                        f"{ref} or {other}: the document says to fit one, not both. "
                        "Check the document.",
                    )
