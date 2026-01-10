# SI Prefix Multipliers
MULTIPLIERS = {
    "p": 1e-12,  # pico
    "n": 1e-9,  # nano
    "u": 1e-6,  # micro (standard)
    "µ": 1e-6,  # micro (alt)
    "m": 1e-3,  # milli
    "k": 1e3,  # kilo
    "K": 1e3,  # kilo (uppercase tolerance)
    "M": 1e6,  # Mega
    "G": 1e9,  # Giga
}

# Core Component Designators (IPC Standard)
CORE_PREFIXES = ("R", "C", "D", "Q", "U", "IC", "SW", "X", "Y", "J")

POT_TAPER_MAP = {
    "A": "Logarithmic",
    "B": "Linear",
    "C": "Reverse Log",
    "W": "W Taper",
    "G": "Graphic",
}

# Chip substitution recommendations
# Keys are the chips found in BOM, values are fun alternatives to try.
# Structure: (Part Name, Sonic Profile, Technical Why)
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

# Diode substitution recommendations
# Keys are the standard BOM parts, values are (Part, Sonic Profile, Technical Why)
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

# Known Switch Labels
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
# Prefixes that indicate a potentiometer regardless of the suffix
POT_PREFIXES = {"POT", "TRIM", "VR", "VOL"}

# Functional names usually associated with Potentiometers
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

# If the ref matches these, it's definitely a knob.
POT_LABELS = POT_PREFIXES | POT_NAMES

# Terms found in PDF descriptions that didn't fit into the strict sets above
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

# Master list for Control Keyword hunting in Build Docs (PDFs)
# Combines all known functional names + extra descriptive terms.
# Sorted list for deterministic regex generation.
KEYWORDS = sorted(list(POT_NAMES | SWITCH_LABELS | KEYWORD_EXTRAS))

IGNORE_VALUES = [
    "RESISTORS",
    "CAPACITORS",
    "DIODES",
    "ICS",
    "POTENTIOMETERS",
    "PARTS",
    "LIST",
    "VALUE",
    "LOCATION",
    "TYPE",
    "RATING",
    "COMPONENTS",
    "OFFBOARD",
    "ENCLOSURE",
    "FOOTSWITCH",
    "JACKS",
    "FEATURES",
    "CONTROLS",
    "REVISION",
    "REV",
    "VERSION",
    "COPYRIGHT",
    "WWW",
    ".COM",
    "EDITION",
]
