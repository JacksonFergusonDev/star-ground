"""Unit tests for TYPE/NOTES column enrichment and value normalization."""

import pytest

from src.bom_lib import (
    ComponentCategory,
    ParseWarningKind,
    categorize_part,
    create_empty_stats,
)
from src.bom_lib.parser import (
    _table_entries,
    capacitor_notes,
    category_from_type,
    enrich_value_from_type,
)
from src.bom_lib.utils import float_to_search_string


@pytest.mark.parametrize(
    ("value", "type_text", "expected"),
    [
        ("RED", "3mm Red LED, Diffused", "3mm Red LED"),
        ("LED", "Indicator LED", "Indicator LED"),
        ("SPST", "SPST Momentary Footswitch (Normally Open)", "SPST Momentary"),
        ("ON/ON", "Toggle switch, SPDT 2-position (ON/ON)", "SPDT ON/ON"),
        ("GE", "Germanium diode, 1N34A", "1N34A"),
        ("A-4.5W-K", "Non-latching 4.5V relay", "A-4.5W-K"),
        ("10K", "Resistor, 1/4W", "10K"),
        ("2P4T", "16mm Mini Rotary switch, 2P4T", "2P4T Mini Rotary"),
        ("Red 3mm", "3mm Red LED, Diffused", "3mm Red LED"),  # Motorizer
        ("BLUE", "3mm LED, Blue", "3mm Blue LED"),  # Bolide
        ("MOMENTARY", "Momentary SPST Footswitch", "SPST MOMENTARY"),  # Fornicus
        ("SPDT", "Toggle switch, 2-position (SPDT)", "SPDT ON/ON"),  # Maleficent Mids
        ("SPDT", "Toggle switch, 3-position (SPDT)", "SPDT"),  # positions not guessed
    ],
)
def test_enrich_value_from_type(value: str, type_text: str, expected: str) -> None:
    assert enrich_value_from_type(value, type_text) == expected


@pytest.mark.parametrize(
    ("value", "type_text", "notes", "expected"),
    [
        ("DPDT", "Toggle switch, DPDT", "2-position (ON/ON)", "DPDT ON/ON"),
        ("SPDT", "Toggle switch, SPDT", "3-position (ON/OFF/ON)", "SPDT ON/OFF/ON"),
        ("220n", "Tantalum capacitor, 5mm", "", "220n Tantalum"),
        ("10u", "Ceramic capacitor, MLCC", "", "10u MLCC"),
        ("1u MLCC", "MLCC capacitor, 7.2 x 2.5mm", "", "1u MLCC"),
        ("10K", "Resistor, 1/4W", "* LED current limiting resistor", "10K"),
        ("SPST", "Foot switch, SPST", "Momentary SPST", "SPST Momentary"),  # Terrarium
        # A latching relay's part number already says so.
        ("TQ2-L-5V", "Single-Coil Latching Relay, 5V", "", "TQ2-L-5V"),
        (
            "B50K",
            "16mm right-angle PCB mount pot",
            "* Dual gang potentiometer",
            "B50K Dual",
        ),
        (
            "B100K DUAL",
            "16mm right-angle PCB mount pot",
            "*Dual gang potentiometer",
            "B100K DUAL",
        ),
        ("SANDED", "Dual op-amp, DIP8", "(JRC4558, etc)", "JRC4558"),
        ("GE", "Germanium diode", "1N34A, 1N270, D9E, etc", "1N34A"),
        ("EXPRESSO", "Expresso Microcontroller", "*Included with PCB", "EXPRESSO"),
        # Aion FX L4: the note describes the original amp's part, not this one.
        (
            "2.2uF",
            "Film capacitor, 7.2 x 5.5mm",
            "Original amp uses 2.7uF tantalum, but 2.2uF film will work the same.",
            "2.2uF",
        ),
    ],
)
def test_enrich_value_from_type_and_notes(
    value: str, type_text: str, notes: str, expected: str
) -> None:
    assert enrich_value_from_type(value, type_text, notes) == expected


def test_current_rating_is_not_a_pot_value() -> None:
    # Auditorium: a 250mA poly fuse is not a 250M audio-taper pot.
    fuse = "Resettable Poly Fuse (250mA)"
    result = categorize_part("POLYFUSE", fuse, category_from_type(fuse))
    assert result is not None
    assert result.category == ComponentCategory.HARDWARE_MISC


@pytest.mark.parametrize(
    ("ref", "value", "hint", "category"),
    [
        # CDXL: a vactrol on an unfamiliar designator.
        ("OC1", "VTL5C4/2", None, ComponentCategory.OPTOELECTRONICS),
        # Nutty Fuzz: a bare 'LDR' with its TYPE cell.
        (
            "LDR",
            "9203",
            category_from_type("Light dependant resistor"),
            ComponentCategory.OPTOELECTRONICS,
        ),
        (
            "LMP1",
            "2174",
            category_from_type("Incandescent lamp (12V, 40mA)"),
            ComponentCategory.HARDWARE_MISC,
        ),
        (
            "FB1",
            "FB",
            category_from_type("Ferrite Bead"),
            ComponentCategory.HARDWARE_MISC,
        ),
        ("U1", "VTL5C10", None, ComponentCategory.OPTOELECTRONICS),  # Constrictor
        ("HARD", "W20KC", None, ComponentCategory.POTENTIOMETERS),  # Dingo
        ("DIP1", "DIP-5", None, ComponentCategory.SWITCHES),  # Muffin Factory
        ("SIDECHAIN", '1/4" Switching TRS', None, ComponentCategory.HARDWARE_MISC),
        ("FREQ", "500R TRIM", None, ComponentCategory.POTENTIOMETERS),  # Frost Drive
        ("R20", "10K TRIM", None, ComponentCategory.POTENTIOMETERS),  # CDXL Reissue
    ],
)
def test_parts_named_by_value_or_type(
    ref: str, value: str, hint: ComponentCategory | None, category: ComponentCategory
) -> None:
    result = categorize_part(ref, value, hint)
    assert result is not None
    assert result.category == category


def test_category_from_type() -> None:
    assert (
        category_from_type("Neutrik 1/4in Insulated Stereo Jack")
        == ComponentCategory.HARDWARE_MISC
    )
    assert (
        category_from_type("Metal film resistor, 1/4W") == ComponentCategory.RESISTORS
    )
    # Hints match at word starts: a red diffused LED is not a fuse.
    assert category_from_type("LED, 5mm, red diffused") == ComponentCategory.DIODES
    assert (
        category_from_type("Light dependent resistor")
        == ComponentCategory.OPTOELECTRONICS
    )
    assert (
        category_from_type("Vactrol, fast on/fast off")
        == ComponentCategory.OPTOELECTRONICS
    )
    assert category_from_type("IC socket, DIP-8") == ComponentCategory.HARDWARE_MISC
    assert category_from_type("DPDT slide, micro") == ComponentCategory.SWITCHES
    assert category_from_type("Germanium") is None


@pytest.mark.parametrize(
    ("ref", "value", "hint", "category", "clean"),
    [
        # A TYPE column decides a label's category over a guess from its value.
        (
            "LEDR",
            "10k",
            ComponentCategory.RESISTORS,
            ComponentCategory.RESISTORS,
            "10k",
        ),
        ("PRES", "50k trimmer", None, ComponentCategory.POTENTIOMETERS, "50k"),
        ("BIAS", "5k trimmer (3386)", None, ComponentCategory.POTENTIOMETERS, "5k"),
        ("C9", "330uF/35V", None, ComponentCategory.CAPACITORS, "330u 35V"),
        # GuitarPCB: numbered trimmers, an LED-brightness trimmer, P-numbered
        # pots, status LEDs under other labels, and a second board's 'R1 (2)'.
        ("T1", "10k Trim", None, ComponentCategory.POTENTIOMETERS, "10k"),
        ("LED", "1k Trimpot", None, ComponentCategory.POTENTIOMETERS, "1k"),
        ("P1", "B100K", None, ComponentCategory.POTENTIOMETERS, "B100K"),
        ("EFL1", "Status Led", None, ComponentCategory.DIODES, "Status Led"),
        ("R1 (2)", "1k", None, ComponentCategory.RESISTORS, "1k"),
    ],
)
def test_aion_rows_classify(
    ref: str, value: str, hint, category: ComponentCategory, clean: str
) -> None:
    result = categorize_part(ref, value, hint)
    assert result is not None
    assert (result.category, result.clean_value) == (category, clean)


def test_named_part_uses_type_hint() -> None:
    result = categorize_part("RECV", "NMJ6HCD2", ComponentCategory.HARDWARE_MISC)
    assert result is not None
    assert result.category == ComponentCategory.HARDWARE_MISC


@pytest.mark.parametrize(
    ("ref", "value", "notes", "expected"),
    [
        ("C102", "10u", "25V Minimum Voltage", "10u (25V minimum)"),
        ("C102", "10u (35V or higher)", "25V", "10u (35V or higher)"),
        ("C1", "1u", "25VDC minimum", "1u (25V minimum)"),  # EV Preamp
        ("R1", "10K", "25V", "10K"),
        ("C1", "100n", "", "100n"),
        # Polar Cap: polarity is part of the purchase.
        ("C6", "4u7", "* 50V minimum, Non-Polar", "4u7 NP (50V minimum)"),
        ("C4", "10u NP", "Non-polar", "10u NP"),
    ],
)
def test_capacitor_notes(ref: str, value: str, notes: str, expected: str) -> None:
    assert capacitor_notes(ref, value, notes) == expected


def test_trimmer_notes_are_not_part_of_the_value() -> None:
    for raw in ("10K Trim (3362P)", "100K (3362P Trim)"):
        result = categorize_part("TR1", raw)
        assert result is not None
        assert result.clean_value in ("10K", "100K")


def test_search_strings_keep_significant_digits() -> None:
    assert float_to_search_string(4650.0) == "4.65k"
    assert float_to_search_string(68100.0) == "68.1k"
    assert float_to_search_string(4700.0) == "4.7k"
    assert float_to_search_string(2.2e-9) == "2.2n"


def test_table_values_drop_optional_markers_but_keep_placeholders() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["R24", "*27K*", "Resistor, 1/4W", "* Omitted in some units"],
        ["D101", "**", "Zener diode", "** Refer to page 2 **"],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.value) for e in entries] == [("R24", "27K"), ("D101", "**")]


def test_table_or_row_marks_the_next_entry() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["D10", "DI1510", "Full-wave bridge rectifier", ""],
        ["", "", "OR", ""],
        ["D10a", "1N4007", "Standard recovery diode, DO-41", ""],
        ["D10b", "1N4007", "Standard recovery diode, DO-41", ""],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.follows_or) for e in entries] == [
        ("D10", False),
        ("D10a", True),
        ("D10b", False),
    ]


def test_table_designator_quantity() -> None:
    table = [
        ["LOCATION", "TYPE", "NOTES"],
        ["TERMINAL BLOCK (Qty 3)", "5mm Screw Terminal Block, 2 position", "[LINK]"],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert ("TERMINAL BLOCK", 3) in [(e.ref, e.qty) for e in entries]


def test_blank_value_takes_the_first_choice_in_notes() -> None:
    # ElectroVibe: the LDR's VALUE cell is empty; NOTES lists the choices.
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["LDR1", "", "Light Dependent Resistor", "(7530, 7532, 9203, or similar)"],
        ["SW1", "", "6mm x 6mm Push Button Switch", "[Link]"],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.value) for e in entries] == [
        ("LDR1", "7530"),
        ("SW1", "6mm x 6mm Push Button Switch"),
    ]


def test_unrecognized_table_row_warns() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["LOCATION", "VALUE", "TYPE", "NOTES"],  # a repeated header is not a part
        ["USB", "USB PORT", "Type-B, Female", "(Link)"],
        ["C9", "", "OMIT", ""],
    ]
    page = {"tables": [table], "text": "", "words": []}
    stats = create_empty_stats()
    _table_entries([page], stats)  # type: ignore[list-item]
    assert [(w.kind, w.ref) for w in stats["warnings"]] == [
        (ParseWarningKind.UNPARSED_VALUE, "USB")
    ]


def test_blank_switch_value_is_completed_from_type_and_notes() -> None:
    # Lone King: 'VOICE | (blank) | Toggle switch, SPDT | 3-position (ON/OFF/ON)'.
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["VOICE", "", "Toggle switch, SPDT", "3-position (ON/OFF/ON)"],
        ["C9", "470n (0.47uF)", "Film capacitor", ""],  # Tone Zester
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.value) for e in entries] == [
        ("VOICE", "SPDT ON/OFF/ON"),
        ("C9", "470n"),
    ]


def test_version_notes_warn() -> None:
    # Lot Lizard: one table for a Nano and a Big Box build.
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["C3", "47p", "Ceramic capacitor", "* Omitted in Nano version"],
        ["BALANCE", "B1K", "16mm pot", "* C1K in Big Box Version"],
        ["C4", "10u", "Electrolytic capacitor", ""],
    ]
    page = {"tables": [table], "text": "", "words": []}
    stats = create_empty_stats()
    _table_entries([page], stats)  # type: ignore[list-item]
    assert [(w.kind, w.ref) for w in stats["warnings"]] == [
        (ParseWarningKind.FOOTNOTE, "C3"),
        (ParseWarningKind.FOOTNOTE, "BALANCE"),
    ]


def test_choose_between_row_marks_the_next_entry() -> None:
    table = [
        ["LOCATION", "VALUE", "TYPE", "NOTES"],
        ["GAIN_TRIM", "250K", "Trimmer potentiometer, 3362P type", ""],
        [
            "Choose between internal trimpot or external potentiometer, do not install both",
            "",
            "",
            "",
        ],
        ["GAIN", "C250K", "16mm right-angle PCB mount pot", ""],
    ]
    page = {"tables": [table], "text": "", "words": []}
    entries = _table_entries([page], create_empty_stats())  # type: ignore[list-item]
    assert [(e.ref, e.follows_or) for e in entries] == [
        ("GAIN_TRIM", False),
        ("GAIN", True),
    ]
