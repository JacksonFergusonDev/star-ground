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
    check_bom_reconciliation,
    check_classification,
    check_duplicate,
    check_footnotes,
    check_marking_code,
    check_row_quantity,
    check_unextracted_refs,
    find_bom_entries,
    find_listed_refs,
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
