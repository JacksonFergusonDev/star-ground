"""Unit tests for component key utilities, RawBOMData protocols, and PresetData typing."""

import io

from src.bom_lib import (
    BOM_PRESETS,
    ComponentCategory,
    PDFPageExtraction,
    PresetCatalog,
    ProjectSlot,
    SupportsGetValue,
    SupportsRead,
    get_preset_metadata,
    make_component_key,
    parse_component_key,
)
from src.bom_lib.enums import InputMethod
from src.bom_lib.strategies.context import BOMParserContext


def test_make_component_key() -> None:
    """Verifies that make_component_key constructs consistent formatted strings."""
    # With Enum
    assert make_component_key(ComponentCategory.RESISTORS, "10k") == "Resistors | 10k"
    assert make_component_key(ComponentCategory.PCB, "Big Muff") == "PCB | Big Muff"

    # With Raw String
    assert make_component_key("Capacitors", "100n") == "Capacitors | 100n"


def test_parse_component_key_valid() -> None:
    """Verifies parse_component_key parses standard formatted keys."""
    cat, val = parse_component_key("Resistors | 10k")
    assert cat == ComponentCategory.RESISTORS
    assert val == "10k"

    cat_pcb, val_pcb = parse_component_key("PCB | Triangulum Boost")
    assert cat_pcb == ComponentCategory.PCB
    assert val_pcb == "Triangulum Boost"


def test_parse_component_key_unknown_or_missing_delimiter() -> None:
    """Verifies fallback behavior for unrecognized categories or raw strings."""
    # Unknown category string
    cat_unk, val_unk = parse_component_key("NonExistentCategory | PartX")
    assert cat_unk == ComponentCategory.UNKNOWN
    assert val_unk == "PartX"

    # Missing delimiter
    cat_raw, val_raw = parse_component_key("JustAPartName")
    assert cat_raw == ComponentCategory.UNKNOWN
    assert val_raw == "JustAPartName"


def test_supports_read_and_getvalue_protocols() -> None:
    """Verifies that file-like objects satisfy SupportsRead and SupportsGetValue."""
    stream = io.BytesIO(b"R1 10k\n")
    assert isinstance(stream, SupportsRead)
    assert isinstance(stream, SupportsGetValue)

    text_stream = io.StringIO("R1 10k\n")
    assert isinstance(text_stream, SupportsRead)
    assert isinstance(text_stream, SupportsGetValue)


def test_project_slot_data_typing() -> None:
    """Verifies ProjectSlot.data can hold various RawBOMData variants without error."""
    slot_str = ProjectSlot(name="Test1", data="R1 10k")
    assert slot_str.data == "R1 10k"

    slot_bytes = ProjectSlot(name="Test2", data=b"R1 10k")
    assert slot_bytes.data == b"R1 10k"

    slot_list = ProjectSlot(name="Test3", data=["R1 10k", "C1 100n"])
    assert slot_list.data == ["R1 10k", "C1 100n"]

    stream = io.BytesIO(b"R1 10k")
    slot_io = ProjectSlot(name="Test4", data=stream)
    assert slot_io.data is stream

    slot_none = ProjectSlot(name="Test5", data=None)
    assert slot_none.data is None


def test_parser_context_with_raw_bom_data() -> None:
    """Verifies BOMParserContext accepts RawBOMData variants."""
    context = BOMParserContext()

    # Str
    res_str = context.process(InputMethod.PASTE_TEXT, "R1 10k", "Manual")
    assert "Resistors | 10k" in res_str.inventory

    # List of str
    res_list = context.process(InputMethod.PASTE_TEXT, ["R1 10k", "R2 4.7k"], "Manual")
    assert "Resistors | 10k" in res_list.inventory
    assert "Resistors | 4.7k" in res_list.inventory

    # None / Empty
    res_empty = context.process(InputMethod.PASTE_TEXT, None, "Empty")
    assert len(res_empty.inventory) == 0


def test_preset_data_typing() -> None:
    """Verifies that PresetData type is used for BOM_PRESETS and contains bom_text."""
    if BOM_PRESETS:
        first_key = next(iter(BOM_PRESETS))
        first_preset = BOM_PRESETS[first_key]
        assert "bom_text" in first_preset
        assert isinstance(first_preset["bom_text"], str)

    catalog = get_preset_metadata()
    assert isinstance(catalog, PresetCatalog)


def test_pdf_page_extraction_type() -> None:
    """Verifies PDFPageExtraction TypedDict keys."""
    extraction: PDFPageExtraction = {
        "tables": [[["R1", "10k"]]],
        "text": "R1 10k",
    }
    assert extraction["tables"][0][0] == ["R1", "10k"]
    assert extraction["text"] == "R1 10k"
