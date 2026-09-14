"""Tests for boundary models, typed structures, and sort utilities."""

from pathlib import Path

from src.bom_lib import constants
from src.bom_lib.enums import ComponentCategory, ComponentOrigin, ComponentSpec
from src.bom_lib.parser import parse_csv_bom, parse_with_verification
from src.bom_lib.presets import PresetCatalog, PresetLookupEntry, get_preset_metadata
from src.bom_lib.sourcing import _format_alts, get_spec_type
from src.bom_lib.types import (
    AlternativeSpec,
    CategorizationResult,
    ChecklistPart,
    ParseResult,
    PurchaseRecommendation,
)
from src.bom_lib.utils import natural_sort_key
from src.pdf_generator import sort_by_z_height


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
    assert "Resistors | 10k" in res_text.inventory

    # parse_csv_bom
    csv_file = tmp_path / "test_bom.csv"
    csv_file.write_text("Designator,Value\nR1,10k\nC1,100n\n", encoding="utf-8")
    res_csv = parse_csv_bom(str(csv_file), source_name="CSV Test")
    assert isinstance(res_csv, ParseResult)
    assert res_csv.stats["parts_found"] == 2
    assert "Resistors | 10k" in res_csv.inventory
