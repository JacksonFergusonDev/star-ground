"""The order a board is built in: lowest parts first.

Soldering the flattest parts first keeps the board level on the bench while
each next, taller part goes in. The field manuals and bench mode both follow
this order, so a builder sees the same sequence on paper and on screen.
"""

from enum import StrEnum

from src.bom_lib.enums import ComponentCategory, ComponentSpec
from src.bom_lib.sourcing import get_spec_type


class BuildStage(StrEnum):
    """A step of a build, declared in the order it's soldered."""

    PCB = "PCB"
    RESISTORS = "Resistors"
    DIODES = "Diodes"
    # Sockets go in before anything tall, and their chips go in last.
    SOCKETS = "Sockets"
    CRYSTALS = "Crystals and oscillators"
    CAPACITORS = "Small capacitors"
    INDUCTORS = "Inductors"
    TRANSISTORS = "Transistors"
    ELECTROLYTICS = "Electrolytic capacitors"
    RELAYS = "Relays"
    SWITCHES = "Switches"
    TRANSFORMERS = "Transformers"
    # Mechanical parts: rigid, and in the way of a soldering iron.
    POTENTIOMETERS = "Potentiometers"
    HARDWARE = "Hardware"
    # Chips go into their sockets last, away from the iron's heat.
    ICS = "Chips"
    OTHER = "Other parts"


_STAGES: dict[ComponentCategory, BuildStage] = {
    ComponentCategory.PCB: BuildStage.PCB,
    ComponentCategory.RESISTORS: BuildStage.RESISTORS,
    ComponentCategory.DIODES: BuildStage.DIODES,
    ComponentCategory.CRYSTALS_OSCILLATORS: BuildStage.CRYSTALS,
    ComponentCategory.CAPACITORS: BuildStage.CAPACITORS,
    ComponentCategory.INDUCTORS: BuildStage.INDUCTORS,
    ComponentCategory.TRANSISTORS: BuildStage.TRANSISTORS,
    ComponentCategory.RELAYS: BuildStage.RELAYS,
    ComponentCategory.SWITCHES: BuildStage.SWITCHES,
    ComponentCategory.TRANSFORMERS: BuildStage.TRANSFORMERS,
    ComponentCategory.POTENTIOMETERS: BuildStage.POTENTIOMETERS,
    ComponentCategory.HARDWARE_MISC: BuildStage.HARDWARE,
    ComponentCategory.ICS: BuildStage.ICS,
}

_ORDER = {stage: index for index, stage in enumerate(BuildStage)}


def build_stage(
    category: ComponentCategory, value: str, spec: ComponentSpec | None = None
) -> BuildStage:
    """The stage a part is soldered in.

    Args:
        category: The part's category.
        value: Its value ('10k', '100u', '8 pin DIP socket').
        spec: Its physical type, if already known; worked out from the value
            for a capacitor otherwise.

    Returns:
        The stage: a socket's own, and an electrolytic capacitor's after the
        transistors, since it stands taller than a film or ceramic one.
    """
    if "SOCKET" in value.upper():
        return BuildStage.SOCKETS
    if category == ComponentCategory.CAPACITORS:
        spec = spec or get_spec_type(category, value)
        if spec == ComponentSpec.ELECTROLYTIC:
            return BuildStage.ELECTROLYTICS
    return _STAGES.get(category, BuildStage.OTHER)


def stage_order(stage: BuildStage) -> int:
    """Where a stage comes in the build: lower is soldered earlier."""
    return _ORDER[stage]
