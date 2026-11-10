"""Unit tests for component key utilities and RawBOMData protocols."""

import io

from star_ground.bom_lib import (
    ComponentCategory,
    ComponentKey,
    SupportsGetValue,
    SupportsRead,
)
from star_ground.bom_lib.enums import InputMethod
from star_ground.bom_lib.strategies.context import BOMParserContext


def test_component_key() -> None:
    """Verifies that a ComponentKey keeps its fields and prints as `Category | Value`."""
    key = ComponentKey(ComponentCategory.RESISTORS, "10k")
    assert isinstance(key, ComponentKey)
    assert key.category == ComponentCategory.RESISTORS
    assert key.value == "10k"
    assert str(key) == "Resistors | 10k"

    key_pcb = ComponentKey(ComponentCategory.PCB, "Big Muff")
    assert key_pcb == ComponentKey(ComponentCategory.PCB, "Big Muff")
    assert str(key_pcb) == "PCB | Big Muff"


def test_supports_read_and_getvalue_protocols() -> None:
    """Verifies that file-like objects satisfy SupportsRead and SupportsGetValue."""
    stream = io.BytesIO(b"R1 10k\n")
    assert isinstance(stream, SupportsRead)
    assert isinstance(stream, SupportsGetValue)

    text_stream = io.StringIO("R1 10k\n")
    assert isinstance(text_stream, SupportsRead)
    assert isinstance(text_stream, SupportsGetValue)


def test_parser_context_with_raw_bom_data() -> None:
    """Verifies BOMParserContext accepts RawBOMData variants."""
    context = BOMParserContext()

    # Str
    res_str = context.process(InputMethod.PASTE_TEXT, "R1 10k", "Manual")
    assert ComponentKey(ComponentCategory.RESISTORS, "10k") in res_str.inventory

    # List of str
    res_list = context.process(InputMethod.PASTE_TEXT, ["R1 10k", "R2 4.7k"], "Manual")
    assert ComponentKey(ComponentCategory.RESISTORS, "10k") in res_list.inventory
    assert ComponentKey(ComponentCategory.RESISTORS, "4.7k") in res_list.inventory

    # None / Empty
    res_empty = context.process(InputMethod.PASTE_TEXT, None, "Empty")
    assert len(res_empty.inventory) == 0
