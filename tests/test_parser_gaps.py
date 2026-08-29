import pytest

from src.bom_lib import parse_with_verification


@pytest.mark.xfail(reason="Regex drops parenthetical state modifiers, see Milestone 2")
def test_gap_truncated_switch_states() -> None:
    # Pythagoras v3: SW1 SPDT (On/Off/On)
    raw_bom = "SW1 SPDT (On/Off/On)"
    inventory, stats = parse_with_verification([raw_bom], source_name="Test")

    # The parser currently grabs "SPDT" and discards the rest.
    # The grammar rewrite must capture the full string.
    assert "Switches | SPDT (On/Off/On)" in inventory
    assert stats["parts_found"] == 1


@pytest.mark.xfail(reason="Regex fails to parse BS 1852 notation, see Milestone 2")
def test_gap_bs1852_notation() -> None:
    # Ungula: RLED 4K7
    raw_bom = "RLED 4K7"
    inventory, stats = parse_with_verification([raw_bom], source_name="Test")

    assert "Resistors | 4.7k" in inventory
    assert stats["parts_found"] == 1


@pytest.mark.xfail(
    reason="Regex miscategorizes switches as potentiometers, see Milestone 2"
)
def test_gap_switch_miscategorization() -> None:
    # Distortr: GAIN SPDT On - On
    raw_bom = "GAIN SPDT On - On"
    inventory, _ = parse_with_verification([raw_bom], source_name="Test")

    # Because the designator is "GAIN", the current classifier heuristic
    # assumes it's a Potentiometer. The grammar needs stronger type inference.
    assert "Switches | SPDT On - On" in inventory
    assert "Potentiometers | SPDT On - On" not in inventory


@pytest.mark.xfail(
    reason="Regex anchors to schematic voltage instead of part number, see Milestone 2"
)
def test_gap_value_extraction_anchoring() -> None:
    # Distortr: D1 1N4739A (9V1)
    raw_bom = "D1 1N4739A (9V1)"
    inventory, _ = parse_with_verification([raw_bom], source_name="Test")

    # Current regex captures "9v1" and misses the actual part number "1N4739A".
    assert "Diodes | 1N4739A" in inventory
    assert "Diodes | 9v1" not in inventory


@pytest.mark.xfail(
    reason="Regex drops dielectric specification suffixes, see Milestone 2"
)
def test_gap_dropped_dielectrics() -> None:
    # Pythagoras v3: C2 1uF MLCC
    raw_bom = "C2 1uF MLCC"
    inventory, _ = parse_with_verification([raw_bom], source_name="Test")

    # Current regex collapses this into generic "1u", dropping the critical MLCC tag.
    assert "Capacitors | 1u MLCC" in inventory
