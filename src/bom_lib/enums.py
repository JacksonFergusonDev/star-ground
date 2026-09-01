"""Domain enums for the BOM library."""

from enum import Enum


class ComponentCategory(Enum):
    """Categories of electronic components."""

    RESISTORS = "Resistors"
    CAPACITORS = "Capacitors"
    POTENTIOMETERS = "Potentiometers"
    SWITCHES = "Switches"
    DIODES = "Diodes"
    TRANSISTORS = "Transistors"
    CRYSTALS_OSCILLATORS = "Crystals/Oscillators"
    HARDWARE_MISC = "Hardware/Misc"
    ICS = "ICs"
    OPTOELECTRONICS = "Optoelectronics"
    UNKNOWN = "Unknown"
    PCB = "PCB"


class ComponentSpec(Enum):
    """Specific sub-specifications for components."""

    MLCC = "MLCC"
    BOX_FILM = "Box Film"
    ELECTROLYTIC = "Electrolytic"
    NONE = ""


class InputMethod(Enum):
    """BOM input methods for project slots and strategies."""

    PASTE_TEXT = "Paste Text"
    UPLOAD_FILE = "Upload File"
    FROM_URL = "From URL"
    PRESET = "Preset"


class FeedbackRating(Enum):
    """User feedback sentiment ratings."""

    TERRIBLE = "😡"
    BAD = "😕"
    NEUTRAL = "😐"
    GOOD = "🙂"
    EXCELLENT = "🤩"
