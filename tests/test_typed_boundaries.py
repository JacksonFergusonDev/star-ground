"""Tests for boundary models, typed structures, and sort utilities."""

from src.bom_lib.enums import ComponentCategory, ComponentOrigin
from src.bom_lib.presets import PresetCatalog, PresetLookupEntry, get_preset_metadata
from src.bom_lib.sourcing import _format_alts
from src.bom_lib.types import (
    AlternativeSpec,
    CategorizationResult,
    ChecklistPart,
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
