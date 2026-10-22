"""Domain enums for the BOM library."""

from enum import Enum, StrEnum


class ComponentCategory(Enum):
    """Categories of electronic components."""

    RESISTORS = "Resistors"
    CAPACITORS = "Capacitors"
    POTENTIOMETERS = "Potentiometers"
    SWITCHES = "Switches"
    DIODES = "Diodes"
    TRANSISTORS = "Transistors"
    CRYSTALS_OSCILLATORS = "Crystals/Oscillators"
    INDUCTORS = "Inductors"
    TRANSFORMERS = "Transformers"
    RELAYS = "Relays"
    HARDWARE_MISC = "Hardware/Misc"
    ICS = "ICs"
    OPTOELECTRONICS = "Optoelectronics"
    UNKNOWN = "Unknown"
    PCB = "PCB"


class ComponentOrigin(StrEnum):
    """Purchasing origins for bill-of-materials components."""

    CIRCUIT_BOARD = "Circuit Board"
    HARDWARE_KIT = "Hardware Kit"
    EXTRAS = "Extras"


class HardwareRole(StrEnum):
    """Off-board hardware the app supplies unless a document lists its own."""

    AUDIO_JACKS = "Audio jacks"
    DC_JACK = "DC jack"
    FOOTSWITCH = "Footswitch"
    ENCLOSURE = "Enclosure"
    STATUS_LED = "Status LED"
    LED_RESISTOR = "LED current-limiting resistor"


class ParseWarningKind(StrEnum):
    """Categories of parser self-check failures surfaced to the user."""

    UNEXTRACTED_REF = "Unextracted designator"
    CONFLICTING_DUPLICATE = "Conflicting duplicate"
    CATEGORY_CONFLICT = "Category conflict"
    UNPARSED_VALUE = "Unparsed value"
    BOM_MISMATCH = "Bill of materials mismatch"
    FOOTNOTE = "Footnote"
    VALUE_CHOICE = "Builder's choice"
    NO_PARTS_LIST = "No parts list"
    OCR_READ = "Read by OCR"
    MISREAD_TEXT = "Misread text layer"
    IMAGE_PARTS_LIST = "Parts list is an image"
    QUANTITY_LIST = "Parts listed by quantity"
    UNREADABLE_ROW = "Unreadable designator"


class OcrVerdict(StrEnum):
    """How far an OCR reading of an image parts list can be trusted."""

    COMPLETE = "Looks complete"
    PARTIAL = "Partial"
    UNUSABLE = "Unusable"


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
