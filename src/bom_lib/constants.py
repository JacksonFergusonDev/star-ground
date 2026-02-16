"""
Static Knowledge Base for the Star Ground Engine.

This module serves as the central repository for:
1.  **Physical Constants:** SI prefix multipliers for component value normalization.
2.  **Parsing Heuristics:** Keyword lists for identifying components, knobs, and switches
    in unstructured PDF text.
3.  **Expert System Data:** "Silicon Sommelier" dictionaries that map generic parts
    (like TL072) to audiophile alternatives with sonic descriptions and technical justifications.
4.  **Exclusion Lists:** Terms used to identify and discard manufacturing artifacts (fiduciaries, test points).
5.  **Purchasing Rules:** Configuration for safe buy quantities (buffers) and round-up logic.
"""

from typing import Any

# --- Physics & Standards ---

# SI Prefix Multipliers
# Maps shorthand prefixes to their float multipliers.
# Includes 'u' (legacy) and 'µ' (correct) for micro-farads.
MULTIPLIERS = {
    "p": 1e-12,  # pico
    "n": 1e-9,  # nano
    "u": 1e-6,  # micro (standard text)
    "µ": 1e-6,  # micro (alt/unicode)
    "m": 1e-3,  # milli
    "k": 1e3,  # kilo
    "K": 1e3,  # kilo (uppercase tolerance)
    "M": 1e6,  # Mega
    "G": 1e9,  # Giga
}

# Core Component Designators (IPC Standard)
# Used to validate if a text token is likely a component reference (e.g., "R1", "C10").
CORE_PREFIXES = ("R", "C", "D", "Q", "U", "IC", "SW", "X", "Y", "J")

# Potentiometer Taper Codes
# Maps the suffix character to the taper curve description.
POT_TAPER_MAP = {
    "A": "Logarithmic",
    "B": "Linear",
    "C": "Reverse Log",
    "W": "W Taper",
    "G": "Graphic",
}

# --- Purchasing & Sourcing Rules ---

PURCHASING_CONFIG: dict[str, dict[str, Any]] = {
    "Resistors": {
        "buffer_add": 5,
        "round_to": 10,
        "note": "Use 1/4W Metal Film (1%)",
        "suspicious_threshold_low": 1.0,  # Ohms
    },
    "Capacitors": {
        "bulk_threshold": 1.0e-7,  # 100nF
        "bulk_buffer": 10,
        "standard_buffer": 5,
        "large_threshold": 1.0e-6,  # 1uF
        "large_buffer": 1,
        "suspicious_threshold_high": 0.01,  # 10mF
    },
    "Diodes": {
        "min_buy": 10,
        "buffer_add": 5,
    },
}

# --- Expert System Data (The "Silicon Sommelier") ---

# Operational Amplifier Substitution Table
# Maps generic BOM parts to a list of "flavor" alternatives.
# Schema: { Generic_Name: [ (Alt_Name, Sonic_Profile, Technical_Justification), ... ] }
IC_ALTS = {
    # Dual Op-Amps
    "TL072": [
        (
            "OPA2134",
            "Hi-Fi / Studio Clean",
            "Low distortion (0.00008%), High Slew Rate (20V/us)",
        ),
        (
            "TLC2272",
            "High Headroom Clean",
            "Rail-to-Rail output (+6Vpp headroom on 9V)",
        ),
    ],
    "JRC4558": [
        (
            "NJM4558D",
            "Vintage Correct",
            "Authentic 1980s BJT bandwidth limiting",
        ),
        (
            "OPA2134",
            "Modern/Clinical",
            "High impedance input, removes 'warm' blur",
        ),
    ],
    # Single Op-Amps (RAT style)
    "LM308": [
        (
            "LM308N",
            "Vintage RAT",
            "Required for 0.3V/us slew-induced distortion",
        ),
        (
            "OP07",
            "Modern Tight",
            "Faster slew rate, sounds harsher/tighter than vintage",
        ),
    ],
    "NE5532": [
        (
            "OPA2134",
            "Lower Noise",
            "JFET input reduces current noise with high-Z guitars",
        ),
    ],
}

# Diode Clipping Profiles
# Maps standard switching diodes to alternatives with distinct clipping characteristics.
# Schema: { Generic_Name: [ (Alt_Name, Sonic_Profile, Technical_Justification), ... ] }
DIODE_ALTS = {
    "1N4148": [
        (
            "1N4001",
            "Smooth / Tube-like",
            "Slow reverse recovery (30µs) smears highs",
        ),
        (
            "IR LED",
            "The 'Goldilocks' Drive",
            "1.2V drop: More crunch than LED, more headroom than Si",
        ),
        (
            "Red LED",
            "Amp-like / Open",
            "1.8V drop: Huge headroom, loud output",
        ),
    ],
    "1N914": [
        (
            "1N4001",
            "Smooth / Tube-like",
            "Slow reverse recovery (30µs) smears highs",
        ),
    ],
    "1N34A": [
        (
            "BAT41",
            "Modern Schottky",
            "Stable alternative, slightly harder knee",
        ),
        (
            "1N60",
            "Alt Germanium",
            "Different Vf variance",
        ),
    ],
}

# --- Parsing Heuristics & Keywords ---

# Known Switch Function Labels
# Used to detect switches in BOM descriptions.
SWITCH_LABELS = {
    "LENGTH",
    "MODE",
    "CLIP",
    "VOICE",
    "BRIGHT",
    "FAT",
    "PV",
    "RANGE",
    "LO",
    "HI",
    "MID",
}

# Known Potentiometer Labels
# Prefixes that indicate a potentiometer regardless of the suffix.
POT_PREFIXES = {"POT", "TRIM", "VR", "VOL"}

# Functional names usually associated with Potentiometers.
# These help identify knobs even if the ref designator is missing or obscured.
POT_NAMES = {
    "VOLUME",
    "TONE",
    "GAIN",
    "DRIVE",
    "DIST",
    "FUZZ",
    "DIRT",
    "LEVEL",
    "MIX",
    "BLEND",
    "BALANCE",
    "DRY",
    "WET",
    "SPEED",
    "RATE",
    "DEPTH",
    "INTENSITY",
    "WIDTH",
    "DECAY",
    "ATTACK",
    "RELEASE",
    "SUSTAIN",
    "COMP",
    "THRESH",
    "TREBLE",
    "BASS",
    "MID",
    "MIDS",
    "PRESENCE",
    "CONTOUR",
    "EQ",
    "BODY",
    "BIAS",
    "BOOST",
    "MASTER",
    "PRE",
    "POST",
    "FILTER",
    "SENS",
    "SWEEP",
    "RES",
    "RESONANCE",
    "AMT",
    "AMOUNT",
    "DISTORTION",
    "OCTAVE",
    "AMPLITUDE",
    "CLEAN",
}

# Union of all Potentiometer identifiers
POT_LABELS = POT_PREFIXES | POT_NAMES

# Contextual Keywords
# Terms found in PDF descriptions that imply controls but don't fit strict categories.
KEYWORD_EXTRAS = {
    "COLOR",
    "TEXTURE",
    "NATURE",
    "THROB",
    "SWELL",
    "PULSE",
    "REPEATS",
    "TIME",
    "FEEDBACK",
    "CUT",
}

# Master Keyword List
# Combines all functional names and extra descriptive terms.
# Sorted list used for deterministic Regex generation in the PDF parser.
KEYWORDS = sorted(list(POT_NAMES | SWITCH_LABELS | KEYWORD_EXTRAS))

# Manufacturing Artifact Exclusion List
# These tokens indicate lines in a BOM that describe non-purchasable items
# (e.g., Test Points, Fiduciaries, PCB layers) or explicitly excluded parts.
IGNORE_VALUES = [
    # --- Manufacturing Artifacts (Ghost Data) ---
    "TP",
    "TPOINT",
    "TEST POINT",
    "TEST",
    "PROBE",
    "FID",
    "FIDUCIAL",
    "MARK",
    "ALIGN",
    "MARKER",
    "MH",
    "HOLE",
    "MOUNTING HOLE",
    "MTG",
    "DRILL",
    "SCREW HOLE",
    "JP",
    "JUMPER",
    "SJ",
    "SOLDER JUMPER",
    "LINK",
    "WIRE",
    "BRIDGE",
    "IO",
    "PAD",
    "VIA",
    # --- Logic Flags & Exclusion ---
    "DNP",
    "DNI",
    "NM",
    "NC",
    "NO POP",
    "DO NOT POPULATE",
    "NOT MOUNTED",
    "OPT",
    "OPTIONAL",
    "OMIT",
    "UNUSED",
    # --- Non-Component Layers & Nets ---
    "PCB",
    "BOARD",
    "PANEL",
    "FACEPLATE",
    "LOGO",
    "GRAPHIC",
    "ART",
    "SILKSCREEN",
    "TEXT",
    "LABEL",
    "ROHS",
    "LEAD FREE",
    "PB-FREE",
    "UL",
    "FCC",
    "CE",
    "TRASH BIN",
    "GND",
    "AGND",
    "DGND",
    "PGND",
    "EARTH",
    "VCC",
    "VDD",
    "VSS",
    "VEE",
    "VREF",
    "VB",
    "VA",
    "+9V",
    "+18V",
    "-9V",
    "+5V",
    "+3V3",
    "BIAS",
    "NOTE",
    "INFO",
    "COMMENT",
    "DESC",
    "DESCRIPTION",
    "DIP",
    "DIP8",
    "DIP14",
    "DIP16",
    "SOIC",
    "SOIC8",
    "PACKAGE",
    "PKG",
]
