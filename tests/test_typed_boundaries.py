"""Tests for boundary models, typed structures, and sort utilities."""

import pytest

from star_ground.bom_lib import constants
from star_ground.bom_lib.enums import ComponentCategory, ComponentOrigin, ComponentSpec
from star_ground.bom_lib.sourcing import _format_alts, get_spec_type
from star_ground.bom_lib.stages import BuildStage, build_stage, stage_order
from star_ground.bom_lib.strategies.csv import parse_csv_bom
from star_ground.bom_lib.strategies.manual import parse_with_verification
from star_ground.bom_lib.types import (
    AlternativeSpec,
    CategorizationResult,
    ComponentKey,
    Inventory,
    ParseResult,
    PurchaseRecommendation,
    RefDesignator,
    SupportsName,
)
from star_ground.bom_lib.utils import natural_sort_key


def test_alternative_spec_formatting() -> None:
    """Formats AlternativeSpec with and without justification."""
    alts = [
        AlternativeSpec(
            name="OPA2134",
            profile="Hi-Fi / Studio Clean",
            justification="Low distortion",
        ),
        AlternativeSpec(
            name="TLC2272",
            profile="High Headroom Clean",
        ),
    ]

    formatted = _format_alts(alts)
    assert "OPA2134 (Hi-Fi / Studio Clean: Low distortion)" in formatted
    assert "TLC2272 (High Headroom Clean)" in formatted


def test_phase1_boundary_models() -> None:
    """Verifies immutability and behavior of Phase 1 typed boundary models."""
    # ComponentOrigin is a StrEnum
    assert isinstance(ComponentOrigin.CIRCUIT_BOARD, str)
    assert ComponentOrigin.CIRCUIT_BOARD.value == "Circuit Board"
    assert ComponentOrigin.HARDWARE_KIT.value == "Hardware Kit"
    assert ComponentOrigin.EXTRAS.value == "Extras"
    assert ComponentOrigin("Circuit Board") is ComponentOrigin.CIRCUIT_BOARD

    # CategorizationResult
    cat_res = CategorizationResult(
        category=ComponentCategory.RESISTORS,
        clean_value="10k",
        injected_key=None,
    )
    assert cat_res.category == ComponentCategory.RESISTORS
    assert cat_res.clean_value == "10k"
    assert cat_res.injected_key is None

    # PurchaseRecommendation
    rec = PurchaseRecommendation(buy_qty=10, note="Buffer added")
    assert rec.buy_qty == 10
    assert rec.note == "Buffer added"


def test_natural_sort_key_types() -> None:
    """Returns list of int and str elements for natural sorting."""
    key = natural_sort_key("R10")
    assert key == ["R", 10, ""]

    refs = ["R10", "R2", "R1"]
    sorted_refs = sorted(refs, key=natural_sort_key)
    assert sorted_refs == ["R1", "R2", "R10"]


def test_purchasing_config_typed_rules() -> None:
    """Verifies that PURCHASING_CONFIG uses ComponentCategory keys and typed rule models."""
    res_rule = constants.PURCHASING_CONFIG[ComponentCategory.RESISTORS]
    assert isinstance(res_rule, constants.ResistorPurchasingRule)
    assert res_rule.buffer_add == 5
    assert res_rule.round_to == 10

    cap_rule = constants.PURCHASING_CONFIG[ComponentCategory.CAPACITORS]
    assert isinstance(cap_rule, constants.CapacitorPurchasingRule)
    assert cap_rule.bulk_buffer == 10
    assert cap_rule.standard_buffer == 5

    diode_rule = constants.PURCHASING_CONFIG[ComponentCategory.DIODES]
    assert isinstance(diode_rule, constants.DiodePurchasingRule)
    assert diode_rule.min_buy == 10
    assert diode_rule.buffer_add == 5


def test_get_spec_type_derivation_without_val_qty() -> None:
    """Verifies get_spec_type derives dielectric accurately when val_qty is not provided."""
    assert (
        get_spec_type(ComponentCategory.CAPACITORS, "100uF")
        == ComponentSpec.ELECTROLYTIC
    )
    assert get_spec_type(ComponentCategory.CAPACITORS, "100n") == ComponentSpec.BOX_FILM
    assert get_spec_type(ComponentCategory.CAPACITORS, "47p") == ComponentSpec.MLCC
    assert get_spec_type(ComponentCategory.RESISTORS, "10k") == ComponentSpec.NONE


def test_parser_functions_return_parse_result() -> None:
    """Verifies that parse_with_verification and parse_csv_bom return ParseResult."""
    # parse_with_verification
    res_text = parse_with_verification(["R1 10k"], source_name="Manual")
    assert isinstance(res_text, ParseResult)
    assert res_text.stats.parts_found == 1
    assert ComponentKey(ComponentCategory.RESISTORS, "10k") in res_text.inventory

    # parse_csv_bom
    res_csv = parse_csv_bom(b"Designator,Value\nR1,10k\nC1,100n\n", "CSV Test")
    assert isinstance(res_csv, ParseResult)
    assert res_csv.stats.parts_found == 2
    assert ComponentKey(ComponentCategory.RESISTORS, "10k") in res_csv.inventory


def test_component_key_domain_model() -> None:
    """Verifies ComponentKey equality, immutability, ordering, and string parsing."""
    import dataclasses

    k1 = ComponentKey(ComponentCategory.RESISTORS, "10k")
    k2 = ComponentKey(ComponentCategory.RESISTORS, "10k")
    k3 = ComponentKey(ComponentCategory.CAPACITORS, "100n")

    # Equality & Hashing
    assert k1 == k2
    assert hash(k1) == hash(k2)
    assert k1 != k3

    # String representation
    assert str(k1) == "Resistors | 10k"

    # Immutability
    with pytest.raises(dataclasses.FrozenInstanceError):
        k1.value = "20k"  # type: ignore[misc]

    # Ordering
    sorted_keys = sorted([k1, k3])
    assert sorted_keys == [k3, k1]  # "Capacitors" < "Resistors"


def test_typed_inventory_operations() -> None:
    """Verifies Inventory operations across strictly typed ComponentKey instances."""
    inv = Inventory()
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    key_c = ComponentKey(ComponentCategory.CAPACITORS, "100n")

    # Add parts using ComponentKey
    inv.add_part("ProjectA", key_r, "R1")
    inv.add_part("ProjectA", key_r, "R2")
    inv.add_part("ProjectB", key_c, "C1")

    # Quantities and metadata
    assert inv[key_r].qty == 2
    assert key_r in inv
    # Non-ComponentKey objects return False for contains
    str_key: object = "Resistors | 10k"
    assert str_key not in inv
    str_cap: object = "Capacitors | 100n"
    assert str_cap not in inv

    # Key iteration yields ComponentKey instances
    all_keys = list(inv.keys())
    assert all(isinstance(k, ComponentKey) for k in all_keys)

    # Dictionary get
    assert inv.get(key_r) is not None
    non_existent = ComponentKey(ComponentCategory.ICS, "TL072")
    assert inv.get(non_existent) is None

    # Deletion with ComponentKey
    del inv[key_c]
    assert key_c not in inv

    # Inventory merge
    inv2 = Inventory()
    inv2.add_part("ProjectC", key_r, "R3")
    inv.merge(inv2, multiplier=2)
    assert inv[key_r].qty == 4  # 2 original + 2 * 1
    assert inv[key_r].sources["ProjectC"] == ["R3", "R3"]


def test_ref_designator_value_object() -> None:
    """Verifies RefDesignator parsing, immutability, formatting, and natural comparison."""
    import dataclasses

    # Standard numbered designators
    r1 = RefDesignator.from_string("R1")
    assert r1.prefix == "R"
    assert r1.number == 1
    assert r1.suffix == ""
    assert str(r1) == "R1"

    c10 = RefDesignator.from_string("C10")
    assert c10.prefix == "C"
    assert c10.number == 10
    assert str(c10) == "C10"

    # Multi-letter prefix
    sw2 = RefDesignator.from_string("SW2")
    assert sw2.prefix == "SW"
    assert sw2.number == 2
    assert str(sw2) == "SW2"

    # Suffixes
    u1_inj = RefDesignator.from_string("U1 (Inj)")
    assert u1_inj.prefix == "U"
    assert u1_inj.number == 1
    assert u1_inj.suffix == "(Inj)"
    assert str(u1_inj) == "U1 (Inj)"

    q1a = RefDesignator.from_string("Q1A")
    assert q1a.prefix == "Q"
    assert q1a.number == 1
    assert q1a.suffix == "A"
    assert str(q1a) == "Q1A"

    # Unnumbered keywords
    hw = RefDesignator.from_string("HW")
    assert hw.prefix == "HW"
    assert hw.number is None
    assert str(hw) == "HW"

    vol = RefDesignator.from_string("VOLUME")
    assert vol.prefix == "VOLUME"
    assert vol.number is None
    assert str(vol) == "VOLUME"

    # Empty string fallback
    empty = RefDesignator.from_string("")
    assert empty.prefix == ""
    assert empty.number is None

    # Natural ordering
    r2 = RefDesignator.from_string("R2")
    r10 = RefDesignator.from_string("R10")
    assert r1 < r2 < r10
    assert c10 < r1

    # Immutability & Hashing
    with pytest.raises(dataclasses.FrozenInstanceError):
        r1.number = 5  # type: ignore[misc]

    assert hash(RefDesignator("R", 1)) == hash(r1)
    ref_set = {r1, r2, r10}
    assert r1 in ref_set


def test_supports_name_protocol() -> None:
    """Verifies SupportsName runtime protocol matching."""

    class NamedItem:
        def __init__(self, name: str) -> None:
            self.name = name

    class UnnamedItem:
        pass

    assert isinstance(NamedItem("test.csv"), SupportsName)
    assert not isinstance(UnnamedItem(), SupportsName)
    assert not isinstance("raw_string", SupportsName)


def test_a_socket_goes_in_before_small_capacitors_and_its_chip_last() -> None:
    stages = [
        build_stage(ComponentCategory.ICS, "TL072"),
        build_stage(ComponentCategory.HARDWARE_MISC, "8 pin DIP Socket"),
        build_stage(ComponentCategory.CAPACITORS, "100n"),
        build_stage(ComponentCategory.CAPACITORS, "100u"),
    ]

    assert sorted(stages, key=stage_order) == [
        BuildStage.SOCKETS,
        BuildStage.CAPACITORS,
        BuildStage.ELECTROLYTICS,
        BuildStage.ICS,
    ]
