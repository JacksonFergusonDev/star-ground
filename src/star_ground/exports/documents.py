"""What the printed documents say: the build book and the label sheet.

Each document is a frozen dataclass of plain text and numbers, built here
from the master list and laid out by a Typst template (`typeset.py`). Every
decision about wording, order and formatting is made here, where it can be
tested without rendering a page; the templates only place what they're given.
"""

import datetime
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from star_ground.bom_lib.constants import INJECTED_SUFFIX
from star_ground.bom_lib.enums import ComponentCategory, ComponentSpec
from star_ground.bom_lib.sourcing import get_spec_type
from star_ground.bom_lib.stages import BuildStage, build_stage, stage_order
from star_ground.bom_lib.types import (
    BuiltProject,
    ComponentKey,
    Inventory,
    RefDesignator,
    ShoppingLine,
)
from star_ground.bom_lib.units import Unit, Value
from star_ground.bom_lib.utils import deduplicate_refs

# --- Values as builders read them ---

_BAND_DIGITS = (
    "black",
    "brown",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "violet",
    "grey",
    "white",
)
_TAPERS = {"A": "audio taper", "B": "linear taper", "C": "reverse audio taper"}
_POT = re.compile(r"^([ABC])?\s*(\d+(?:[.,]\d+)?)\s*([kKM]?)\s*([ABC])?\b\s*(.*)$")
_POT_POWER = {"": 0, "k": 3, "K": 3, "M": 6}
_PINS = re.compile(r"(\d+)\s*-?\s*PIN", re.IGNORECASE)


def resistor_bands(value: Value) -> tuple[str, ...]:
    """A 1% metal film resistor's five colour bands, read left to right.

    Three digits, the multiplier, then brown for 1%.

    Args:
        value: A resistance.

    Returns:
        The bands' colour names, or nothing when the value isn't a resistance
        or needs more than three digits.
    """
    if value.unit != Unit.OHM or value.magnitude <= 0:
        return ()
    magnitude = value.magnitude.normalize()
    power = magnitude.adjusted() - 2
    digits = magnitude.scaleb(-power)
    if digits != digits.to_integral_value() or not -2 <= power <= 9:
        return ()
    multiplier = {-2: "silver", -1: "gold"}.get(power) or _BAND_DIGITS[power]
    return (*(_BAND_DIGITS[int(d)] for d in str(int(digits))), multiplier, "brown")


def capacitor_code(value: Value) -> str:
    """The code printed on a ceramic or film capacitor ('104' for 100 nF).

    Args:
        value: A capacitance.

    Returns:
        Two digits and a count of zeros, in picofarads; the plain number
        under 100 pF ('47'); or nothing when the value has no such code.
    """
    if value.unit != Unit.FARAD:
        return ""
    picofarads = (value.magnitude * Decimal("1e12")).normalize()
    if picofarads < 1 or picofarads != picofarads.to_integral_value():
        return ""
    if picofarads < 100:
        return str(int(picofarads))
    power = picofarads.adjusted() - 1
    digits = picofarads.scaleb(-power)
    if digits != digits.to_integral_value() or power > 9:
        return ""
    return f"{int(digits)}{power}"


@dataclass(frozen=True, slots=True)
class ReadablePart:
    """A part's value written for a builder.

    Attributes:
        text: The value ('4.7 kΩ', '100 nF', 'TL072').
        detail: What else identifies it ('linear taper, dual'), if anything.
        bands: A resistor's colour bands.
        code: The code printed on a small capacitor.
    """

    text: str
    detail: str = ""
    bands: tuple[str, ...] = ()
    code: str = ""


def readable_part(
    category: ComponentCategory, value: str, quantity: Value | Decimal | None
) -> ReadablePart:
    """Writes a part's value the way a builder reads it.

    Args:
        category: The part's category.
        value: Its value as the master list holds it ('4.7k', 'B100K DUAL').
        quantity: The value with its unit, when it has one.

    Returns:
        The part in words, with its colour bands or code where it has them.
    """
    if isinstance(quantity, Value):
        if quantity.unit == Unit.OHM:
            return ReadablePart(str(quantity), bands=resistor_bands(quantity))
        spec = get_spec_type(category, value, val_qty=quantity)
        if spec == ComponentSpec.ELECTROLYTIC:
            return ReadablePart(str(quantity), "electrolytic")
        kind = {ComponentSpec.MLCC: "ceramic", ComponentSpec.BOX_FILM: "film"}
        return ReadablePart(
            str(quantity), kind.get(spec, ""), code=capacitor_code(quantity)
        )
    if category == ComponentCategory.POTENTIOMETERS:
        pot = _POT.match(value.strip())
        if pot:
            before, number, prefix, after, rest = pot.groups()
            ohms = Value(
                Decimal(number.replace(",", ".")).scaleb(_POT_POWER[prefix]),
                Unit.OHM,
            )
            taper = before or after
            details = [_TAPERS[taper]] if taper else []
            if rest:
                details.append(rest.lower())
            return ReadablePart(str(ohms), ", ".join(details))
    if "SOCKET" in value.upper():
        pins = _PINS.search(value)
        name = f"{pins.group(1)}-pin DIP socket" if pins else "DIP socket"
        return ReadablePart(name, "" if pins else "match each chip's pin count")
    return ReadablePart(value)


def condense_refs(refs: Iterable[str]) -> str:
    """Writes designators with runs collapsed: 'C1, Q3-Q4, R1-R3'.

    Args:
        refs: Designators ('R1', 'R2', 'IC1').

    Returns:
        The designators sorted by prefix and number, runs of three or more
        written as ranges, then anything else (a pot's 'TIME') as it is.
    """
    numbered: dict[str, list[int]] = {}
    other: list[str] = []
    for ref in refs:
        des = RefDesignator.from_string(ref)
        if des.number is not None and not des.suffix and des.prefix.isalpha():
            numbered.setdefault(des.prefix, []).append(des.number)
        else:
            other.append(ref)
    parts: list[str] = []
    for prefix in sorted(numbered, key=str.upper):
        run: list[int] = []
        for n in [*sorted(set(numbered[prefix])), None]:
            if n is not None and run and n == run[-1] + 1:
                run.append(n)
                continue
            if len(run) >= 3:
                parts.append(f"{prefix}{run[0]}-{prefix}{run[-1]}")
            else:
                parts.extend(f"{prefix}{r}" for r in run)
            run = [n] if n is not None else []
    return ", ".join([*parts, *sorted(set(other))])


# --- The build book ---

_STAGE_HINTS: dict[BuildStage, str] = {
    BuildStage.RESISTORS: "Bend each pair of legs to the holes, solder, then trim.",
    BuildStage.DIODES: "The band marks the cathode: match it to the stripe on the board.",
    BuildStage.SOCKETS: "Match the notch to the outline, and tack two opposite corners first.",
    BuildStage.CAPACITORS: "Film and ceramic capacitors fit either way round.",
    BuildStage.TRANSISTORS: "Match each flat face to the outline, and leave a few millimetres of leg.",
    BuildStage.ELECTROLYTICS: "Long leg to the + pad; the stripe marks the negative leg.",
    BuildStage.POTENTIOMETERS: "Fit the board in the enclosure first, so the shafts line up with the holes.",
    BuildStage.ICS: "Last, into their sockets: pin 1 to the notch.",
}

_ORIENTATION: dict[BuildStage, str] = {
    BuildStage.DIODES: "Band to the stripe",
    BuildStage.SOCKETS: "Notch to the outline",
    BuildStage.TRANSISTORS: "Flat face to the outline",
    BuildStage.ELECTROLYTICS: "Long leg to +",
    BuildStage.ICS: "Pin 1 to the notch",
}


# Voltage regulators sold as chips come in a transistor's three-leg package.
_REGULATOR = re.compile(r"^(?:L?7[89]L?\d+|LM317|LM337)", re.IGNORECASE)


def _orientation(stage: BuildStage, value: str) -> str:
    if stage == BuildStage.DIODES and "LED" in value.upper():
        return "Long leg to +"
    if stage == BuildStage.ICS and _REGULATOR.match(value):
        return _ORIENTATION[BuildStage.TRANSISTORS]
    return _ORIENTATION.get(stage, "")


@dataclass(frozen=True, slots=True)
class ChecklistRow:
    """One line of a project's checklist: a value and where it goes.

    Attributes:
        qty: How many one pedal takes.
        part: The value, written for a builder.
        refs: Where they go ('R1-R3, R7').
        orientation: Which way round, for a part that has one.
    """

    qty: int
    part: ReadablePart
    refs: str
    orientation: str


@dataclass(frozen=True, slots=True)
class Stage:
    """One step of a build: the parts soldered together.

    Attributes:
        number: Its place in the build, from 1.
        name: What's soldered ('Resistors').
        hint: One line of how.
        rows: Its parts, smallest value first.
    """

    number: int
    name: str
    hint: str
    rows: tuple[ChecklistRow, ...]


@dataclass(frozen=True, slots=True)
class ProjectChecklist:
    """A project's parts in the order they're soldered.

    Attributes:
        name: The project as the master list names it.
        count: How many pedals of it are being built.
        parts: How many parts one pedal takes.
        stages: Its stages, in build order.
    """

    name: str
    count: int
    parts: int
    stages: tuple[Stage, ...]


@dataclass(frozen=True, slots=True)
class ShoppingRow:
    """A part to buy, as the build book lists it.

    Attributes:
        part: The value, written for a builder.
        needed: How many the builds use.
        in_stock: How many the builder's stock holds.
        buy: How many to buy, spares included.
        notes: Buying notes.
    """

    part: ReadablePart
    needed: int
    in_stock: int
    buy: int
    notes: str


@dataclass(frozen=True, slots=True)
class ShoppingGroup:
    """The shopping list's parts of one category.

    Attributes:
        category: The category ('Resistors').
        rows: Its parts, in shopping list order.
    """

    category: str
    rows: tuple[ShoppingRow, ...]


@dataclass(frozen=True, slots=True)
class PackFile:
    """A file in the build pack, as the book's cover lists it.

    Attributes:
        name: The file's name.
        about: What it's for.
    """

    name: str
    about: str


@dataclass(frozen=True, slots=True)
class BuildBook:
    """Everything the build book prints.

    Attributes:
        generated: When it was made ('7 November 2026').
        version: Star Ground's version.
        projects: Each project's checklist, in master list order.
        shopping: What to buy, by category.
        lines_to_buy: How many shopping list lines have anything to buy.
        pieces_to_buy: How many parts to buy in all.
        files: The rest of the build pack, when the book is part of one.
    """

    generated: str
    version: str
    projects: tuple[ProjectChecklist, ...]
    shopping: tuple[ShoppingGroup, ...]
    lines_to_buy: int
    pieces_to_buy: int
    files: tuple[PackFile, ...] = ()


@dataclass(frozen=True, slots=True)
class _ProjectPart:
    key: ComponentKey
    quantity: Value | Decimal | None
    refs: tuple[str, ...]
    qty: int


def unique_projects(projects: Sequence[BuiltProject]) -> list[BuiltProject]:
    """Each project once, by label, in first-seen order.

    A project entered twice has its quantities summed on the master list, so
    it gets one checklist and one source document.
    """
    seen: set[str] = set()
    unique: list[BuiltProject] = []
    for project in projects:
        if project.label not in seen:
            seen.add(project.label)
            unique.append(project)
    return unique


def _project_parts(kit: Inventory, label: str) -> list[_ProjectPart]:
    """One pedal's parts of a project, from the master list."""
    parts = []
    for key, data in kit.items():
        if label not in data.sources:
            continue
        refs = deduplicate_refs(data.sources[label])
        counted = data.counted.get(label, 0)
        if refs or counted:
            refs = [r.removesuffix(INJECTED_SUFFIX) for r in refs]
            parts.append(
                _ProjectPart(key, data.val_qty, tuple(refs), len(refs) + counted)
            )
    return parts


def _sort_key(part: _ProjectPart) -> tuple[int, Decimal, str]:
    quantity = part.quantity
    magnitude = quantity.magnitude if isinstance(quantity, Value) else quantity
    return (0 if magnitude is not None else 1, magnitude or Decimal(0), part.key.value)


def project_checklist(kit: Inventory, project: BuiltProject) -> ProjectChecklist:
    """A project's parts grouped into the stages they're soldered in.

    Args:
        kit: The master list's parts, hardware kit included.
        project: The project.

    Returns:
        Its checklist for one pedal.
    """
    by_stage: dict[BuildStage, list[_ProjectPart]] = {}
    for part in _project_parts(kit, project.label):
        stage = build_stage(part.key.category, part.key.value)
        by_stage.setdefault(stage, []).append(part)
    stages = []
    for number, stage in enumerate(sorted(by_stage, key=stage_order), start=1):
        rows = tuple(
            ChecklistRow(
                qty=part.qty,
                part=readable_part(part.key.category, part.key.value, part.quantity),
                refs=condense_refs(part.refs),
                orientation=_orientation(stage, part.key.value),
            )
            for part in sorted(by_stage[stage], key=_sort_key)
        )
        stages.append(Stage(number, stage.value, _STAGE_HINTS.get(stage, ""), rows))
    return ProjectChecklist(
        name=project.label,
        count=project.hardware.count,
        parts=sum(row.qty for stage in stages for row in stage.rows),
        stages=tuple(stages),
    )


def shopping_groups(
    lines: Sequence[ShoppingLine], kit: Inventory
) -> tuple[ShoppingGroup, ...]:
    """The shopping list grouped by category, in its own order.

    Args:
        lines: The shopping list.
        kit: The master list's parts, for each value's unit.

    Returns:
        One group per category.
    """
    groups: dict[str, list[ShoppingRow]] = {}
    for line in lines:
        data = kit.get(ComponentKey(line.category, line.part))
        quantity = data.val_qty if data else None
        groups.setdefault(line.category.value, []).append(
            ShoppingRow(
                part=readable_part(line.category, line.part, quantity),
                needed=line.bom_qty,
                in_stock=line.in_stock,
                buy=line.buy_qty,
                notes=line.notes,
            )
        )
    return tuple(ShoppingGroup(c, tuple(rows)) for c, rows in groups.items())


def build_book(
    kit: Inventory,
    projects: Sequence[BuiltProject],
    lines: Sequence[ShoppingLine],
    generated: datetime.date,
    version: str,
    files: Sequence[PackFile] = (),
) -> BuildBook:
    """Everything the build book prints, from the master list.

    Args:
        kit: The master list's parts, hardware kit included.
        projects: The master list's projects.
        lines: The shopping list.
        generated: The day it's made.
        version: Star Ground's version.
        files: The rest of the build pack, when the book is part of one.

    Returns:
        The book's contents.
    """
    return BuildBook(
        generated=f"{generated.day} {generated:%B %Y}",
        version=version,
        projects=tuple(project_checklist(kit, p) for p in unique_projects(projects)),
        shopping=shopping_groups(lines, kit),
        lines_to_buy=sum(1 for line in lines if line.buy_qty > 0),
        pieces_to_buy=sum(line.buy_qty for line in lines),
        files=tuple(files),
    )


# --- The label sheet ---


@dataclass(frozen=True, slots=True)
class Label:
    """One label for a bag or drawer of parts.

    Attributes:
        project: The project the parts are for.
        stage: The build stage they're soldered in.
        part: The value, written for a builder.
        refs: Where they go.
        qty: How many one pedal takes.
    """

    project: str
    stage: str
    part: ReadablePart
    refs: str
    qty: int


@dataclass(frozen=True, slots=True)
class LabelSheet:
    """Labels for Avery 5160 sheets (30 to a US Letter page).

    Attributes:
        labels: Every label, project by project, in build order.
    """

    labels: tuple[Label, ...]


def label_sheet(kit: Inventory, projects: Sequence[BuiltProject]) -> LabelSheet:
    """A label for each of each project's parts, in the order they're soldered.

    Args:
        kit: The master list's parts.
        projects: The master list's projects.

    Returns:
        The sheet's labels.
    """
    labels: list[Label] = []
    for project in unique_projects(projects):
        for stage in project_checklist(kit, project).stages:
            labels.extend(
                Label(project.label, stage.name, row.part, row.refs, row.qty)
                for row in stage.rows
            )
    return LabelSheet(tuple(labels))
