"""Unit tests for the parser self-checks in `src/bom_lib/verification.py`."""

import pytest

from src.bom_lib import (
    ComponentCategory,
    ParseWarningKind,
    create_empty_inventory,
    create_empty_stats,
    make_component_key,
    parse_with_verification,
)
from src.bom_lib.verification import (
    check_alternate_value,
    check_bom_reconciliation,
    check_build_changes,
    check_build_note,
    check_classification,
    check_duplicate,
    check_footnotes,
    check_marking_code,
    check_numbered_footnotes,
    check_numbering_gaps,
    check_part_versions,
    check_parts_found,
    check_prose_pots,
    check_row_quantity,
    check_two_values,
    check_unextracted_refs,
    check_value_assignments,
    check_value_keyed_list,
    check_valueless_refs,
    check_version_note,
    find_bom_entries,
    find_listed_refs,
    resolve_value_choice,
)


def _kinds(stats) -> list[ParseWarningKind]:
    return [w.kind for w in stats["warnings"]]


class TestFindListedRefs:
    """Tests for designator listing detection."""

    def test_column_layout_row(self) -> None:
        text = "R1 1M C1 100n Q1 BS170 VOLUME A100K"
        assert find_listed_refs(text) == {"R1", "C1", "Q1", "VOLUME"}

    def test_silkscreen_runs_are_ignored(self) -> None:
        assert find_listed_refs("R13 C4 R12 D2 R18") == set()

    def test_unknown_control_names_detected_by_value_shape(self) -> None:
        text = "SHIFT B25K\nSUBS SPDT (On - Off - On)\nMODE 1P8T Mini Rotary"
        assert find_listed_refs(text) == {"SHIFT", "SUBS", "MODE"}

    def test_bare_designator_requires_numeric_value(self) -> None:
        assert find_listed_refs("LED 3mm Red LED") == {"LED"}
        assert find_listed_refs("LED F O") == set()

    def test_bare_designator_only_where_a_listing_can_begin(self) -> None:
        # After another listing's value, as in a column layout.
        assert find_listed_refs("R7 4K7 LED 3mm") == {"R7", "LED"}
        # Inside a value or description, a bullet list, or before a parenthetical.
        assert find_listed_refs("LED1 3mm Red LED 3mm Red LED, Diffused") == {"LED1"}
        assert find_listed_refs("D1 3mm LED 3mm Red LED, Diffused") == {"D1"}
        assert find_listed_refs("• Toggle Switch 1 • LED 1") == set()
        assert find_listed_refs("TRIMA 100K TRIM (3362P)") == {"TRIMA"}

    def test_designator_shaped_value_is_not_a_listing(self) -> None:
        assert find_listed_refs("Q4 J201 JFET transistor") == {"Q4"}

    def test_prose_and_headers_are_ignored(self) -> None:
        text = "Resistors Capacitors\nParameter 1\n1 x 100K"
        assert find_listed_refs(text) == set()


class TestUnextractedRefs:
    """Tests for the designator conservation check."""

    def test_warns_for_listed_but_missing_ref(self) -> None:
        inventory = create_empty_inventory()
        inventory.add_part(
            "T", make_component_key(ComponentCategory.RESISTORS, "1M"), "R1"
        )
        stats = create_empty_stats()

        check_unextracted_refs(["R1 1M\nR13 330"], inventory, stats)

        assert [(w.kind, w.ref) for w in stats["warnings"]] == [
            (ParseWarningKind.UNEXTRACTED_REF, "R13")
        ]

    def test_schematic_pages_are_not_parts_lists(self) -> None:
        stats = create_empty_stats()
        page = "CLR 2\nIC2A TL072\nSheet: /\nFile: HotTeacher.kicad_sch\nKiCad E.D.A. 9.0.7"
        check_unextracted_refs([page], create_empty_inventory(), stats)
        assert stats["warnings"] == []

    def test_part_number_shaped_like_a_designator(self) -> None:
        # Golden Falk: 'Q1-Q5  J201  JFET transistor' lists J201 as a value.
        inventory = create_empty_inventory()
        inventory.add_part(
            "T", make_component_key(ComponentCategory.TRANSISTORS, "J201"), "Q1"
        )
        stats = create_empty_stats()
        check_unextracted_refs(["Q1-Q5 J201 JFET transistor"], inventory, stats)
        assert stats["warnings"] == []

    def test_op_amp_sections_of_an_extracted_chip(self) -> None:
        inventory = create_empty_inventory()
        inventory.add_part(
            "T", make_component_key(ComponentCategory.ICS, "TL072"), "IC2"
        )
        stats = create_empty_stats()
        check_unextracted_refs(["IC2A TL072\nIC3B TL072"], inventory, stats)
        assert [w.ref for w in stats["warnings"]] == ["IC3B"]

    def test_lowercase_suffix_matches_extracted_ref(self) -> None:
        # Shindig lists 'D10a'; the inventory may hold it in either case.
        inventory = create_empty_inventory()
        inventory.add_part(
            "T", make_component_key(ComponentCategory.DIODES, "1N4007"), "D10A"
        )
        stats = create_empty_stats()

        check_unextracted_refs(
            ["D10a 1N4007 Standard recovery diode"], inventory, stats
        )

        assert stats["warnings"] == []

    def test_injected_refs_do_not_count_as_extracted(self) -> None:
        inventory = create_empty_inventory()
        key = make_component_key(ComponentCategory.HARDWARE_MISC, "DIP SOCKET")
        inventory.add_part("T", key, "IC1 (Inj)")
        stats = create_empty_stats()

        check_unextracted_refs(["IC1 TL072"], inventory, stats)

        assert [w.ref for w in stats["warnings"]] == ["IC1"]


class TestCheckDuplicate:
    """Tests for conflicting duplicate detection."""

    def test_conflicting_values_warn(self) -> None:
        stats = create_empty_stats()
        check_duplicate(stats, "SW1", "2P4T", "SPDT")
        assert _kinds(stats) == [ParseWarningKind.CONFLICTING_DUPLICATE]

    @pytest.mark.parametrize(
        "new_value", ["B5K", "b5k", "and", "Revised4/9/21"], ids=str
    )
    def test_equivalent_values_prose_and_dates_do_not_warn(
        self, new_value: str
    ) -> None:
        stats = create_empty_stats()
        check_duplicate(stats, "MIX", "B5K", new_value)
        assert stats["warnings"] == []


class TestCheckClassification:
    """Tests for classifier contradiction detection."""

    def test_designator_category_contradiction(self) -> None:
        stats = create_empty_stats()
        check_classification(
            stats, "D1", "1N34A", ComponentCategory.POTENTIOMETERS, "1N34A"
        )
        assert _kinds(stats) == [ParseWarningKind.CATEGORY_CONFLICT]

    def test_switch_value_in_wrong_category(self) -> None:
        stats = create_empty_stats()
        check_classification(
            stats, "GAIN", "SPDT (On - On)", ComponentCategory.POTENTIOMETERS, "x"
        )
        assert _kinds(stats) == [ParseWarningKind.CATEGORY_CONFLICT]

    def test_unparseable_passive_value(self) -> None:
        stats = create_empty_stats()
        check_classification(stats, "R9", "22K*", ComponentCategory.RESISTORS, "22K*")
        assert _kinds(stats) == [ParseWarningKind.UNPARSED_VALUE]

    def test_placeholder_value_warns(self) -> None:
        # Son of Ben: 'BASS | *** | *** | * Refer to Page 3'.
        stats = create_empty_stats()
        check_classification(
            stats, "BASS", "***", ComponentCategory.POTENTIOMETERS, "***"
        )
        assert _kinds(stats) == [ParseWarningKind.UNPARSED_VALUE]

    def test_consistent_part_does_not_warn(self) -> None:
        stats = create_empty_stats()
        check_classification(stats, "R1", "4K7", ComponentCategory.RESISTORS, "4.7k")
        check_classification(
            stats, "LDR1", "5mm", ComponentCategory.OPTOELECTRONICS, "5mm"
        )
        assert stats["warnings"] == []


class TestBomReconciliation:
    """Tests for the quantity bill-of-materials cross-check."""

    BOM_PAGE = (
        "Bill of Materials\n"
        "1 x 82 1 x 470p 3 x BS170 1 x A100K\n"
        "2 x 10K 1 x 1N4739A 1 x B100K Dual-Ganged\n"
        "1 x SPDT Toggle Switch (On - On)\n"
        "1 x 3mm Red LED"
    )

    def _inventory(self, parts: list[tuple[ComponentCategory, str, str]]):
        inventory = create_empty_inventory()
        for category, value, ref in parts:
            inventory.add_part("T", make_component_key(category, value), ref)
        return inventory

    def test_value_missing_from_bom_warns(self) -> None:
        inventory = self._inventory([(ComponentCategory.DIODES, "9v1 Zener", "D1")])
        stats = create_empty_stats()
        check_bom_reconciliation([self.BOM_PAGE], inventory, stats)
        assert [(w.kind, w.ref) for w in stats["warnings"]] == [
            (ParseWarningKind.BOM_MISMATCH, "D1")
        ]

    def test_equivalent_values_agree(self) -> None:
        inventory = self._inventory(
            [
                (ComponentCategory.RESISTORS, "10k", "R1"),
                (ComponentCategory.CAPACITORS, "470pF", "C1"),
                (ComponentCategory.TRANSISTORS, "BS170", "Q1"),
                (ComponentCategory.POTENTIOMETERS, "B100K Dual", "RATE"),
                (ComponentCategory.SWITCHES, "SPDT (On - On)", "GAIN"),
                (ComponentCategory.DIODES, "3mm Red LED", "D3"),
            ]
        )
        stats = create_empty_stats()
        check_bom_reconciliation([self.BOM_PAGE], inventory, stats)
        assert stats["warnings"] == []

    def test_bare_led_warns_when_the_bom_names_the_led(self) -> None:
        # Distortr: 'D3  LED' beside a BOM entry '1 x 3mm Red LED'.
        inventory = self._inventory([(ComponentCategory.DIODES, "LED", "D3")])
        stats = create_empty_stats()
        check_bom_reconciliation([self.BOM_PAGE], inventory, stats)
        assert [(w.kind, w.ref) for w in stats["warnings"]] == [
            (ParseWarningKind.BOM_MISMATCH, "D3")
        ]

    def test_dimensions_outside_a_bom_page_are_not_entries(self) -> None:
        text = "C8 22n Film capacitor, 7.2 x 2.5mm\n" * 10
        assert find_bom_entries([text]) == []


def test_ingestion_records_conflicting_duplicates() -> None:
    """Duplicate designators flow through ingestion into warnings."""
    res = parse_with_verification(["SW1 2P4T", "SW1 SPDT"], source_name="Test")
    assert [(w.kind, w.ref) for w in res.stats["warnings"]] == [
        (ParseWarningKind.CONFLICTING_DUPLICATE, "SW1")
    ]


class TestDesignatorShapes:
    """The self-check's own designator rules (kept independent of the parser)."""

    def test_suffixed_and_named_designators_are_listings(self) -> None:
        assert find_listed_refs("R1A 390K C10A 6n8 IC_A MCP41010") == {
            "R1A",
            "C10A",
            "IC_A",
        }

    def test_c_taper_values_and_jumper_pads_are_not_designators(self) -> None:
        assert find_listed_refs("PRES B10K * C10K recommended") == {"PRES"}
        assert find_listed_refs("JP01 SOLDER") == set()


class TestFootnotes:
    """A value marked with an asterisk whose note sits below the parts list."""

    def test_footnote_is_surfaced(self) -> None:
        text = (
            "GLIDE SPDT On/On\nOSC-ROOT *DPDT On/On/On\n1P8T Rotary\n"
            "*DPDT On/On/On\nThis switch MUST be a Type-2 DPDT On/On/On toggle switch."
        )
        stats = create_empty_stats()
        check_footnotes([text], stats)
        (warning,) = stats["warnings"]
        assert warning.kind == ParseWarningKind.FOOTNOTE
        assert warning.ref == "OSC-ROOT"
        assert "Type-2" in warning.message

    def test_asterisk_without_a_matching_value_is_ignored(self) -> None:
        stats = create_empty_stats()
        check_footnotes(["R24 27K\n* Omitted in some units\nmore text"], stats)
        assert stats["warnings"] == []


def test_row_quantity_must_match_its_designators() -> None:
    stats = create_empty_stats()
    check_row_quantity(stats, "R10-R12,R18", 4, 4)
    check_row_quantity(stats, "C4,C5", 2, None)
    assert stats["warnings"] == []
    check_row_quantity(stats, "C9,C12", 2, 3)
    assert [(w.kind, w.ref) for w in stats["warnings"]] == [
        (ParseWarningKind.BOM_MISMATCH, "C9,C12")
    ]


@pytest.mark.parametrize(
    ("value", "marking", "warns"),
    [
        ("330", "334", True),  # Q-Tune: 330pF printed, 330nF marked
        ("100n", "104", False),
        ("470n", "474", False),
        ("1u", "105", False),
        ("100p", "101", False),
    ],
)
def test_marking_code_must_match_the_value(
    value: str, marking: str, warns: bool
) -> None:
    stats = create_empty_stats()
    check_marking_code(stats, "C3", value, marking)
    assert (_kinds(stats) == [ParseWarningKind.BOM_MISMATCH]) is warns


def test_vactrol_on_an_ic_designator_is_not_a_conflict() -> None:
    # Constrictor: 'U1  VTL5C10' under OPTOISOLATORS.
    stats = create_empty_stats()
    check_classification(
        stats, "U1", "VTL5C10", ComponentCategory.OPTOELECTRONICS, "VTL5C10"
    )
    assert stats["warnings"] == []


class TestAionChecks:
    """Self-checks added for Aion FX parts tables."""

    def test_two_word_labels_and_bare_led_account_for_their_words(self) -> None:
        inventory = create_empty_inventory()
        inventory.add_part(
            "doc", make_component_key(ComponentCategory.SWITCHES, "DPDT"), "HI CUT"
        )
        inventory.add_part(
            "doc", make_component_key(ComponentCategory.DIODES, "5mm LED"), "LED"
        )
        stats = create_empty_stats()
        check_unextracted_refs(["HI CUT DPDT slide\nLED1 5mm\nCUT 1"], inventory, stats)
        assert stats["warnings"] == []

    @pytest.mark.parametrize(
        ("value", "chosen"),
        [
            ("1M to 2M2", "1M"),
            ("1k8 - 4k7", "1k8"),
            ("22-33k", "22k"),
            ("51pF (or 47pF) MLCC", "51pF MLCC"),
            ("2N5088/2N2222", "2N5088"),
            ("SPST or SPDT", "SPST"),
            ("15k or 22k", "15k"),
            ("270nF/220nF", "270nF"),  # Five Cats Albus
        ],
    )
    def test_builder_choices_use_the_first_value(self, value: str, chosen: str) -> None:
        stats = create_empty_stats()
        assert resolve_value_choice(stats, "RPD", value) == chosen
        assert _kinds(stats) == [ParseWarningKind.VALUE_CHOICE]

    def test_a_restated_value_is_no_choice(self) -> None:
        stats = create_empty_stats()
        assert resolve_value_choice(stats, "C3", "100n/0.1uF") == "100n"
        assert stats["warnings"] == []

    @pytest.mark.parametrize("value", ["2N5088", "B100K", "1-2", "1/4", "220u/35V"])
    def test_values_that_are_not_choices(self, value: str) -> None:
        stats = create_empty_stats()
        assert resolve_value_choice(stats, "R1", value) == value
        assert stats["warnings"] == []

    @pytest.mark.parametrize(
        "note",
        [
            "100n in guitar version.",
            "150k for silicon version.",
            "Analogman version uses 1.2M here.",
            "GC-1 uses 150k.",
            "Early G-2 uses 620R. Later G-2 uses 1k8.",
            "Omit (leave empty) for P-2.",
            "Omit if using Morning Glory gain stage.",
            "Part of the optional Morning Glory gain stage.",
            "100pF in the SD-9M (not present in the original).",
            "10M in Catalinbread and Orpheum.",
            "Orpheum: 10M; Axis/Foxey: omit (leave empty)",
            "Use a 100n film capacitor here if building a 3-transistor variant.",
            # Aion FX Triton: builds' values anywhere in the note.
            "Carbon comp in original, measured value 560R Formula 5F6: 1k8 DLS Red: 1k",
            "Formula 5F6: 4.7uF electrolytic",
            "Formula 5F6: (omit)",
            # Aion FX Malacandra and Aurum.
            "RC Booster and Bass RC use 22k.",
            "RC Booster and Bass RC jumper this.",
            "Bass RC omits this diode.",
            "Use 16k for original OD-2.",
        ],
    )
    def test_notes_that_name_another_build(self, note: str) -> None:
        stats = create_empty_stats()
        check_version_note(stats, "R1", note)
        assert _kinds(stats) == [ParseWarningKind.FOOTNOTE]

    @pytest.mark.parametrize(
        "note",
        [
            "Original uses MA856 diodes.",
            "Substitute. Original uses 2N4401.",
            "Early units used 10k here.",
            "Most vintage units use 4k7.",
            "1M in original RAT.",
            "Can also use 1uF with no change in tone.",
            "Recommended to use a lower value. See build notes.",
            "Set to about 40% rotation (11:00) for stock circuit.",
            "Any germanium diode should work the same in this circuit.",
            "Alternate: 4K7",
            "Sub: MPSA18",
            "Quantity: 2",
            "Leave empty or omit this.",
            "Use 100n for C4 if you want more bass.",
            "Original OD-2 uses 22n. See build notes.",
            "Galaxie mod: 10k. See build notes for full mod.",
        ],
    )
    def test_notes_that_name_no_build(self, note: str) -> None:
        stats = create_empty_stats()
        check_version_note(stats, "R1", note)
        assert stats["warnings"] == []


@pytest.mark.parametrize(
    "text",
    [
        "Always jump J1 through J4 for a standard build.",
        "R12 1k8* CLR TR1 100R*",
        "R24 R24 is only available on v3 PCB or earlier",
        "simply attaching J4 wiring to the Tone TwEQ Volume pad 3 instead. You have now",
    ],
)
def test_prose_is_not_a_listing(text: str) -> None:
    listed = find_listed_refs(text)
    assert not listed & {"J1", "J4", "CLR", "R24"}


def test_an_unreadable_document_says_so() -> None:
    stats = create_empty_stats()
    check_parts_found(stats, 0)
    assert _kinds(stats) == [ParseWarningKind.NO_PARTS_LIST]
    stats = create_empty_stats()
    check_parts_found(stats, 12)
    assert stats["warnings"] == []


def _inventory(*parts: tuple[ComponentCategory, str, str]):
    inv = create_empty_inventory()
    for category, value, ref in parts:
        inv[make_component_key(category, value)]["refs"].append(ref)
    return inv


def test_value_assignments_for_another_version() -> None:
    # Aion FX legacy Rift: the Shin-Ei FY-2 changes, listed under a build note.
    inv = _inventory(
        (ComponentCategory.RESISTORS, "22k", "R1"),
        (ComponentCategory.RESISTORS, "3k3", "R9"),
        (ComponentCategory.CAPACITORS, "1n", "C2"),
    )
    stats = create_empty_stats()
    text = "Shin-Ei FY-2: a few small value changes:\nR1 = 100k\nC2 = 1n\nR9 = jumper"
    check_value_assignments([text], inv, stats)
    assert [w.ref for w in stats["warnings"]] == ["R1", "R9"]


def test_valueless_refs_are_named_unless_read() -> None:
    inv = _inventory((ComponentCategory.DIODES, "1N914", "D6"))
    stats = create_empty_stats()
    check_valueless_refs(["D6", "D7"], inv, stats)
    assert [w.ref for w in stats["warnings"]] == ["D7"]


def test_pots_given_only_in_prose() -> None:
    # Aion FX legacy Halo's variant tables list no pots.
    text = "For all variants, the Sustain and Tone pots are 100kB and Volume is 100kA."
    stats = create_empty_stats()
    check_prose_pots(
        [text], _inventory((ComponentCategory.RESISTORS, "1k", "R1")), stats
    )
    assert [w.ref for w in stats["warnings"]] == ["SUSTAIN", "TONE", "VOLUME"]
    stats = create_empty_stats()
    with_pot = _inventory((ComponentCategory.POTENTIOMETERS, "100kB", "TONE"))
    check_prose_pots([text], with_pot, stats)
    assert stats["warnings"] == []


def test_a_sparse_series_names_its_gaps() -> None:
    # GuitarPCB G2 Animal's text layer lists only a table of substitutes.
    parts = [(ComponentCategory.RESISTORS, "10M", f"R{n}") for n in (1, 4, 8, 12)]
    stats = create_empty_stats()
    check_numbering_gaps(_inventory(*parts), stats)
    assert {w.ref for w in stats["warnings"]} == {
        f"R{n}" for n in (2, 3, 5, 6, 7, 9, 10, 11)
    }
    # A list with a few gaps is complete enough.
    stats = create_empty_stats()
    dense = [
        (ComponentCategory.RESISTORS, "1k", f"R{n}") for n in range(1, 12) if n != 7
    ]
    check_numbering_gaps(_inventory(*dense), stats)
    assert stats["warnings"] == []
    # With capacitors listed, sparse numbering is the design's own
    # (PedalPCB Thermionic Deluxe: R32-R37).
    stats = create_empty_stats()
    cap = (ComponentCategory.CAPACITORS, "100n", "C1")
    check_numbering_gaps(_inventory(*parts, cap), stats)
    assert stats["warnings"] == []


def test_two_values_side_by_side() -> None:
    inv = _inventory(
        (ComponentCategory.RESISTORS, "7.5k 8k2", "R6"),
        (ComponentCategory.CAPACITORS, "22n 63V", "C1"),
        (ComponentCategory.RESISTORS, "620 Lower Value", "R36"),
    )
    stats = create_empty_stats()
    check_two_values(inv, stats)
    assert [w.ref for w in stats["warnings"]] == ["R6"]


def test_pictured_list_warns_document_and_shared_designators() -> None:
    # GuitarPCB Sonic Architect: the Sonic Bloom list is an image beside the
    # AmpEQ text list on page 3.
    from src.bom_lib.verification import check_pictured_lists

    stats = create_empty_stats()
    check_pictured_lists(
        {2: ["Bill of Materials Sonic Bloom"]}, {2: {"R1", "C3", "BASS"}}, stats
    )
    assert [(w.kind, w.ref) for w in stats["warnings"]] == [
        (ParseWarningKind.IMAGE_PARTS_LIST, ""),
        (ParseWarningKind.IMAGE_PARTS_LIST, "C3"),
        (ParseWarningKind.IMAGE_PARTS_LIST, "R1"),
    ]


def test_other_list_warns_changed_and_absent_parts() -> None:
    # GuitarPCB Stage 3: page 5 lists the previous board, unread.
    from src.bom_lib.verification import check_other_list

    read = {f"R{n}": "1k" for n in range(1, 7)} | {"C2": "10n", "D2": "1n4001"}
    listed = [(f"R{n}", "1k") for n in range(1, 6)] + [("C2", "22n")]
    stats = create_empty_stats()
    check_other_list(4, listed, read, stats)
    assert [w.ref for w in stats["warnings"]] == ["", "C2", "D2", "R6"]
    # The same value in other units is the same part.
    stats = create_empty_stats()
    check_other_list(4, [("C1", "22µ")], {"C1": "22u"}, stats)
    assert [w.ref for w in stats["warnings"]] == [""]


def test_either_part_note() -> None:
    from src.bom_lib.verification import check_either_part

    inv = _inventory(
        (ComponentCategory.POTENTIOMETERS, "3k", "TR1"),
        (ComponentCategory.RESISTORS, "1k", "R18"),
    )
    stats = create_empty_stats()
    text = (
        "TR1 / R18 \u2013 either the trim pot or fixed resistor is used but not both."
    )
    check_either_part([text], inv, stats)
    assert {(w.kind, w.ref) for w in stats["warnings"]} == {
        (ParseWarningKind.VALUE_CHOICE, "TR1"),
        (ParseWarningKind.VALUE_CHOICE, "R18"),
    }


def test_alternative_part_number_is_no_missing_designator() -> None:
    # GuitarPCB FrontStage: 'Q1 | J309 or J113' names a JFET, not jack J113.
    inv = _inventory((ComponentCategory.TRANSISTORS, "J309", "Q1"))
    stats = create_empty_stats()
    check_unextracted_refs(["Q1 J309 or J113"], inv, stats)
    assert stats["warnings"] == []


def test_comma_listed_part_numbers_are_a_choice() -> None:
    stats = create_empty_stats()
    assert resolve_value_choice(stats, "IC1", "7660S, 1044") == "7660S"
    assert [w.kind for w in stats["warnings"]] == [ParseWarningKind.VALUE_CHOICE]
    # A slash between bare numbers stays as printed.
    assert resolve_value_choice(create_empty_stats(), "R1", "100/220") == "100/220"


def test_build_note_on_a_row_whose_builds_differ() -> None:
    # Aion FX Elite: C13 '1n | 1n | 820pF', 'Mod value is MLCC, not film.'
    stats = create_empty_stats()
    check_build_note(
        stats, "C13", ["1n", "1n", "820pF"], "Mod value is MLCC, not film."
    )
    assert _kinds(stats) == [ParseWarningKind.FOOTNOTE]
    quiet = create_empty_stats()
    check_build_note(quiet, "C11", ["10pF", "10pF", "10pF"], "Mod value is MLCC.")
    check_build_note(quiet, "R1", ["1k", "2k"], "")
    assert quiet["warnings"] == []


def test_alternate_value_is_warned() -> None:
    stats = create_empty_stats()
    check_alternate_value(stats, "R10", "2k", "47R")
    assert [(w.kind, w.ref) for w in stats["warnings"]] == [
        (ParseWarningKind.FOOTNOTE, "R10")
    ]


def test_footnotes_on_building_without_a_part() -> None:
    # Aion FX legacy Lumin: values carry markers, footnotes say how to omit.
    text = "\n".join(
        [
            "R19 1k 1 Midrange knob: The original unit only has two knobs. By putting",
            "R20 47k a 15k resistor in R15 we can adjust the midrange. If you do want to",
            "leave it off, use 39k for R15 and jumper lugs 2 and 3.",
            "2 High frequency switch: I recommend socketing C4, CX1 and CX2. To omit",
            "this control, just leave off CX1, CX2 and the High Freq switch.",
            "4 C10 was added in the 2015 revision.",
        ]
    )
    marked = [("R15", "1"), ("Midrange", "1"), ("C4", "2"), ("CX1", "2"), ("C10", "4")]
    inventory = create_empty_inventory()
    for ref, value, cat in (
        ("R15", "15k", ComponentCategory.RESISTORS),
        ("MIDRANGE", "50kB", ComponentCategory.POTENTIOMETERS),
        ("C4", "3n3", ComponentCategory.CAPACITORS),
        ("CX1", "1n5", ComponentCategory.CAPACITORS),
        ("C10", "100n", ComponentCategory.CAPACITORS),
    ):
        inventory[make_component_key(cat, value)]["refs"].append(ref)
    stats = create_empty_stats()
    check_numbered_footnotes([marked], [text], inventory, stats)
    assert sorted(w.ref for w in stats["warnings"]) == ["CX1", "MIDRANGE", "R15"]


def _listed(*parts: tuple[str, str, ComponentCategory]):
    inventory = create_empty_inventory()
    for ref, value, cat in parts:
        inventory[make_component_key(cat, value)]["refs"].append(ref)
    return inventory


def test_build_notes_change_list_for_another_version() -> None:
    # Aion FX Quartz ('Bass variant' bullets) and Aurum ('Building the original
    # OD-2' prose); a modification's list (Sunstone) is not another version.
    text = "\n".join(
        [
            "Bass variant",
            "The bass version shares the same overall schematic.",
            "• R34: 1k5 → 100R",
            "• D2-4: omit (leave empty)",
            "Building the original OD-2",
            "1. Use 22n for C12 instead of 27n.",
            "2. Jumper R8 instead of using a 4k7 resistor.",
            "Color switch modification",
            "• R23: 3k3 → 2k2",
        ]
    )
    inventory = _listed(
        ("R34", "1k5", ComponentCategory.RESISTORS),
        ("D3", "1N914", ComponentCategory.DIODES),
        ("C12", "27n", ComponentCategory.CAPACITORS),
        ("R8", "4k7", ComponentCategory.RESISTORS),
        ("R23", "3k3", ComponentCategory.RESISTORS),
    )
    stats = create_empty_stats()
    check_build_changes([text], inventory, stats)
    assert sorted(w.ref for w in stats["warnings"]) == ["C12", "D3", "R34", "R8"]


def test_part_numbered_version_swaps_its_part() -> None:
    # Aion FX Lithium: 'Use 1k for MN3207 version' on R16; IC2 is an MN3007.
    rows = [
        ("R16", "JUMPER", "Use 1k for MN3207 version. See build notes."),
        ("IC2", "MN3007", ""),
        ("IC3", "TL072", ""),
    ]
    stats = create_empty_stats()
    check_part_versions(stats, rows)
    assert [w.ref for w in stats["warnings"]] == ["IC2"]


def test_capacitance_under_a_resistor_designator() -> None:
    # Aion FX legacy Stratus's SD-1 table: 'R8  18n capacitor'.
    stats = create_empty_stats()
    check_classification(
        stats, "R8", "18n capacitor", ComponentCategory.RESISTORS, "18n capacitor"
    )
    assert _kinds(stats) == [ParseWarningKind.CATEGORY_CONFLICT]
    quiet = create_empty_stats()
    # A diode number is no capacitance (GuitarPCB Emexar v4: 'R10  1N4001').
    check_classification(quiet, "R10", "1N4001", ComponentCategory.RESISTORS, "1N4001")
    assert not any("capacitor's" in w.message for w in quiet["warnings"])


def test_omitted_and_running_title_refs_are_accounted_for() -> None:
    # Aion FX Gale: the schematic labels C1, which the list leaves empty;
    # legacy L5 Preamp's running footer 'L5 PREAMP 5' is no listing of L5.
    pages = [f"R1 10k\nC1 120pF\nL5 PREAMP {n}" for n in range(1, 5)]
    inventory = _listed(("R1", "10k", ComponentCategory.RESISTORS))
    stats = create_empty_stats()
    check_unextracted_refs(pages, inventory, stats, frozenset({"C1"}))
    assert stats["warnings"] == []


def test_value_keyed_parts_list_cannot_be_read() -> None:
    # Aion FX 3PDT Bypass: 'PART QTY TYPE NOTES' with values in PART.
    stats = create_empty_stats()
    check_value_keyed_list(
        ["PARTS LIST\nPART QTY TYPE NOTES\n1N5817 (D1) 1 Schottky"], stats
    )
    assert _kinds(stats) == [ParseWarningKind.NO_PARTS_LIST]
    quiet = create_empty_stats()
    check_value_keyed_list(["PART VALUE TYPE NOTES\nR1 10k Metal film"], quiet)
    assert quiet["warnings"] == []
