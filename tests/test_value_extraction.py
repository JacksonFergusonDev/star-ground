"""Regression tests for value extraction from manually entered BOM lines.

These cases come from real PedalPCB documents where the original regex parser
truncated or misclassified values. They now pass and must keep passing.
"""

from src.bom_lib import (
    ComponentCategory,
    ComponentKey,
    parse_with_verification,
)


def test_switch_state_modifiers_are_kept() -> None:
    # Pythagoras v3: SW1 SPDT (On/Off/On)
    raw_bom = "SW1 SPDT (On/Off/On)"
    res = parse_with_verification([raw_bom], source_name="Test")
    inventory, stats = res.inventory, res.stats

    assert ComponentKey(ComponentCategory.SWITCHES, "SPDT (On/Off/On)") in inventory
    assert stats["parts_found"] == 1


def test_bs1852_notation_on_bare_designator() -> None:
    # Ungula: RLED 4K7
    raw_bom = "RLED 4K7"
    res = parse_with_verification([raw_bom], source_name="Test")
    inventory, stats = res.inventory, res.stats

    assert ComponentKey(ComponentCategory.RESISTORS, "4.7k") in inventory
    assert stats["parts_found"] == 1


def test_switch_value_outranks_control_name() -> None:
    # Distortr: GAIN SPDT On - On
    raw_bom = "GAIN SPDT On - On"
    inventory = parse_with_verification([raw_bom], source_name="Test").inventory

    # "GAIN" names a pot on most pedals, but an SPDT value makes it a switch.
    assert ComponentKey(ComponentCategory.SWITCHES, "SPDT On - On") in inventory
    assert (
        ComponentKey(ComponentCategory.POTENTIOMETERS, "SPDT On - On") not in inventory
    )


def test_value_anchors_on_part_number() -> None:
    # Distortr: D1 1N4739A (9V1)
    raw_bom = "D1 1N4739A (9V1)"
    inventory = parse_with_verification([raw_bom], source_name="Test").inventory

    # The value is anchored on the part number, keeping the annotation.
    (key,) = inventory.keys()
    assert key.category == ComponentCategory.DIODES
    assert key.value.startswith("1N4739A")


def test_dielectric_qualifier_is_kept() -> None:
    # Pythagoras v3: C2 1uF MLCC
    raw_bom = "C2 1uF MLCC"
    inventory = parse_with_verification([raw_bom], source_name="Test").inventory

    # The MLCC tag decides the footprint, so it must survive normalization.
    assert ComponentKey(ComponentCategory.CAPACITORS, "1u MLCC") in inventory
