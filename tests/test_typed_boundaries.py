"""Tests for boundary models, typed structures, and sort utilities."""

from pathlib import Path

import pytest

from src.bom_lib import constants
from src.bom_lib.enums import ComponentCategory, ComponentOrigin, ComponentSpec
from src.bom_lib.parser import parse_csv_bom, parse_with_verification
from src.bom_lib.presets import PresetCatalog, PresetLookupEntry, get_preset_metadata
from src.bom_lib.sourcing import _format_alts, get_spec_type
from src.bom_lib.types import (
    AlternativeSpec,
    CategorizationResult,
    ChecklistPart,
    ComponentKey,
    Inventory,
    ParseResult,
    PurchaseRecommendation,
    RefDesignator,
    SupportsName,
)
from src.bom_lib.utils import natural_sort_key
from src.pdf_generator import condense_refs, sort_by_z_height


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


def test_preset_catalog_structure() -> None:
    """Verifies get_preset_metadata returns a typed PresetCatalog with PresetLookupEntry items."""
    catalog = get_preset_metadata()

    assert isinstance(catalog, PresetCatalog)
    assert isinstance(catalog.sources, list)
    assert isinstance(catalog.categories, dict)
    assert isinstance(catalog.lookup, list)

    if catalog.lookup:
        entry: PresetLookupEntry = catalog.lookup[0]
        assert "full_key" in entry
        assert "source" in entry
        assert "category" in entry
        assert "name" in entry


def test_checklist_part_z_height_sorting() -> None:
    """Sorts ChecklistPart items according to hardware assembly sequence."""
    parts: list[ChecklistPart] = [
        {
            "category": ComponentCategory.ICS,
            "value": "TL072",
            "qty": 1,
            "refs": ["U1"],
            "notes": "",
            "polarized": True,
        },
        {
            "category": ComponentCategory.RESISTORS,
            "value": "10k",
            "qty": 2,
            "refs": ["R1", "R2"],
            "notes": "",
            "polarized": False,
        },
        {
            "category": ComponentCategory.PCB,
            "value": "Triangulum",
            "qty": 1,
            "refs": ["PCB"],
            "notes": "",
            "polarized": False,
        },
        {
            "category": ComponentCategory.HARDWARE_MISC,
            "value": "8 PIN DIP SOCKET",
            "qty": 1,
            "refs": ["U1 (Inj)"],
            "notes": "[!] Check Size",
            "polarized": False,
        },
    ]

    sorted_parts = sort_by_z_height(parts)
    categories_in_order = [p["category"] for p in sorted_parts]

    # PCB (0) -> Resistors (10) -> Sockets (18) -> ICs (90)
    assert categories_in_order == [
        ComponentCategory.PCB,
        ComponentCategory.RESISTORS,
        ComponentCategory.HARDWARE_MISC,
        ComponentCategory.ICS,
    ]


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


def test_capacitor_z_height_sorting_by_spec() -> None:
    """Verifies that electrolytic caps rank after ceramic/film caps without string heuristics."""
    parts: list[ChecklistPart] = [
        {
            "category": ComponentCategory.CAPACITORS,
            "value": "100uF",
            "qty": 1,
            "refs": ["C3"],
            "notes": "",
            "polarized": True,
            "spec_type": ComponentSpec.ELECTROLYTIC,
        },
        {
            "category": ComponentCategory.CAPACITORS,
            "value": "100n",
            "qty": 1,
            "refs": ["C2"],
            "notes": "",
            "polarized": False,
            "spec_type": ComponentSpec.BOX_FILM,
        },
        {
            "category": ComponentCategory.RESISTORS,
            "value": "10k",
            "qty": 1,
            "refs": ["R1"],
            "notes": "",
            "polarized": False,
        },
    ]

    sorted_parts = sort_by_z_height(parts)
    # Resistors (10) -> Film cap (40) -> Electro cap (60)
    assert [p["value"] for p in sorted_parts] == ["10k", "100n", "100uF"]


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


def test_parser_functions_return_parse_result(tmp_path: Path) -> None:
    """Verifies that parse_with_verification and parse_csv_bom return ParseResult."""
    # parse_with_verification
    res_text = parse_with_verification(["R1 10k"], source_name="Manual")
    assert isinstance(res_text, ParseResult)
    assert res_text.stats["parts_found"] == 1
    assert ComponentKey(ComponentCategory.RESISTORS, "10k") in res_text.inventory

    # parse_csv_bom
    csv_file = tmp_path / "test_bom.csv"
    csv_file.write_text("Designator,Value\nR1,10k\nC1,100n\n", encoding="utf-8")
    res_csv = parse_csv_bom(str(csv_file), source_name="CSV Test")
    assert isinstance(res_csv, ParseResult)
    assert res_csv.stats["parts_found"] == 2
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

    # String representation & round-trip
    assert str(k1) == "Resistors | 10k"
    parsed_k1 = ComponentKey.from_string("Resistors | 10k")
    assert parsed_k1 == k1

    # Immutability
    with pytest.raises(dataclasses.FrozenInstanceError):
        k1.value = "20k"  # type: ignore[misc]

    # Fallback for unrecognized categories
    fallback = ComponentKey.from_string("UnknownCat | 123")
    assert fallback.category == ComponentCategory.UNKNOWN
    assert fallback.value == "123"

    raw_single = ComponentKey.from_string("BareValue")
    assert raw_single.category == ComponentCategory.UNKNOWN
    assert raw_single.value == "BareValue"

    # Ordering
    sorted_keys = sorted([k1, k3])
    assert sorted_keys == [k3, k1]  # "Capacitors" < "Resistors"


def test_typed_inventory_coercion_and_operations() -> None:
    """Verifies Inventory operations across ComponentKey and legacy string coercion."""
    inv = Inventory()
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")

    # Add parts using both ComponentKey and string
    inv.add_part("ProjectA", key_r, "R1")
    inv.add_part("ProjectA", "Resistors | 10k", "R2")
    inv.add_part("ProjectB", "Capacitors | 100n", "C1")

    # Quantities and metadata
    assert inv[key_r]["qty"] == 2
    assert inv["Resistors | 10k"]["qty"] == 2
    assert key_r in inv
    str_key: object = "Resistors | 10k"
    assert str_key in inv
    str_cap: object = "Capacitors | 100n"
    assert str_cap in inv

    # Key iteration yields ComponentKey instances
    all_keys = list(inv.keys())
    assert all(isinstance(k, ComponentKey) for k in all_keys)

    # Dictionary get with coercion
    assert inv.get(key_r) is not None
    assert inv.get("Resistors | 10k") is not None
    assert inv.get("NonExistentKey") is None

    # Deletion with coercion
    del inv["Capacitors | 100n"]
    assert str_cap not in inv
    assert ComponentKey(ComponentCategory.CAPACITORS, "100n") not in inv

    # Inventory merge
    inv2 = Inventory()
    inv2.add_part("ProjectC", key_r, "R3")
    inv.merge(inv2, multiplier=2)
    assert inv[key_r]["qty"] == 4  # 2 original + 2 * 1
    assert inv[key_r]["sources"]["ProjectC"] == ["R3", "R3"]


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


def test_condense_refs_behavior() -> None:
    """Verifies condense_refs properly collapses consecutive runs and handles unnumbered parts."""
    # Standard consecutive collapse across prefixes
    refs = ["R1", "R2", "R3", "C1", "Q3", "Q4"]
    assert condense_refs(refs) == "C1, Q3-Q4, R1-R3"

    # Non-consecutive numbers
    assert condense_refs(["R1", "R3", "R5"]) == "R1, R3, R5"

    # Unnumbered and mixed items
    assert condense_refs(["HW", "R1", "R2"]) == "HW, R1-R2"

    # Single item and empty
    assert condense_refs(["R1"]) == "R1"
    assert condense_refs([]) == ""


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
