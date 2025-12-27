import pytest
from hypothesis import given, strategies as st
from src.bom_lib import (
    parse_with_verification,
    get_buy_details,
    parse_value_to_float,
    float_to_search_string,
    float_to_display_string,
    get_standard_hardware,
)

# Standard Unit Tests


def test_basic_resistor_parsing():
    """Does it handle a perfect input?"""
    raw_text = "R1 10k"
    inventory, stats = parse_with_verification([raw_text])

    assert inventory["Resistors | 10k"] == 1
    assert stats["parts_found"] == 1
    assert len(stats["residuals"]) == 0


def test_pcb_trap():
    """Does it handle the multi-line PCB header?"""
    raw_text = "PCB\nBig Muff Board"
    inventory, stats = parse_with_verification([raw_text])

    assert inventory["PCB | Big Muff Board"] == 1


# [tests/test_parser.py]


def test_2n5457_behavior():
    """
    Ensure we do NOT auto-replace 2N5457 or inject adapters.
    The user is smart; we just warn them in the notes.
    """
    # Case 1: Vintage THT Part
    raw_text = "Q1 2N5457"
    inventory, _ = parse_with_verification([raw_text])

    # Should stay as 2N5457
    assert inventory["Transistors | 2N5457"] == 1
    # Should NOT inject adapter
    assert inventory.get("Hardware/Misc | SMD_ADAPTER_BOARD", 0) == 0

    # Case 2: Modern SMD Part
    raw_text_2 = "Q2 MMBF5457"
    inventory_2, _ = parse_with_verification([raw_text_2])

    # Should stay as MMBF5457
    assert inventory_2["Transistors | MMBF5457"] == 1
    # Should NOT inject adapter (User might have SOT-23 pads)
    assert inventory_2.get("Hardware/Misc | SMD_ADAPTER_BOARD", 0) == 0


def test_warning_flags():
    """Verify that get_buy_details generates the correct warnings."""
    # Test Obsolete Warning
    _, note = get_buy_details("Transistors", "2N5457", 1)
    assert "Obsolete" in note

    # Test SMD Warning
    _, note = get_buy_details("Transistors", "MMBF5457", 1)
    assert "SMD Part" in note


# 2. Stress Testing


@given(st.text())
def test_parser_never_crashes(garbage_string):
    """
    STRESS TEST: Feed the parser absolute garbage (emojis, chinese characters,
    binary data, massive strings) and ensure it NEVER raises an exception.
    """
    try:
        inventory, stats = parse_with_verification([garbage_string])

        assert isinstance(inventory, dict)
        assert isinstance(stats, dict)

    except Exception as e:
        # If the parser crashes, this test fails and prints the input that killed it
        pytest.fail(f"Parser crashed on input: {garbage_string!r} with error: {e}")


@given(st.integers(min_value=1, max_value=1000))
def test_buy_logic_scaling(qty):
    """
    STRESS TEST: Verify that 'Buy Qty' is ALWAYS >= 'BOM Qty'
    regardless of how many parts we order.
    """
    category = "Resistors"
    val = "10k"

    buy_qty, note = get_buy_details(category, val, qty)

    # We should never buy FEWER than we need
    assert buy_qty >= qty


def test_float_engine_round_trip():
    """
    Verifies the full lifecycle of a value.
    Raw -> Float -> Display
    """
    # Test Case: 1.5k Resistor
    val = parse_value_to_float("1k5")

    # TYPE GUARD: Tell Mypy "If this is None, crash the test right here"
    assert val is not None

    assert val == 1500.0
    assert float_to_search_string(val) == "1.5k"
    assert float_to_display_string(val) == "1k5"

    # Test Case: 100n (Capacitor Normalization)
    val1 = parse_value_to_float("100n")

    # TYPE GUARD
    assert val1 is not None

    # We normalized 100n -> 1.0e-7.
    # Our renderer might output "100n" or "0.1u" depending on implementation details,
    # but it will definitely be one of them.
    out = float_to_search_string(val1)
    assert "u" in out or "n" in out


def test_bs1852_formatting():
    """Does the 'Pretty' renderer handle the decimal swap?"""
    val = 1500.0  # 1.5k
    assert float_to_display_string(val) == "1k5"

    val = 2200000.0  # 2.2M
    assert float_to_display_string(val) == "2M2"

    val = 4700.0  # 4.7k
    assert float_to_display_string(val) == "4k7"


def test_suspicious_physics_warnings():
    """
    Ensure we flag physically improbable values.
    """
    # 1. Resistor too small (0.1 Ohm)
    # Note: 0.1 -> parse_value_to_float -> 0.1
    _, note_r = get_buy_details("Resistors", "0.1", 1)
    assert "Suspicious" in note_r
    assert "< 1Ω" in note_r

    # 2. Capacitor too huge (1 Farad)
    # Note: "1F" -> parse_value_to_float -> 1.0 (Huge!)
    _, note_c = get_buy_details("Capacitors", "1F", 1)
    assert "Suspicious" in note_c
    assert "> 10mF" in note_c

    # 3. Normal values should be fine
    _, note_ok = get_buy_details("Resistors", "10k", 1)
    assert "Suspicious" not in note_ok


def test_resistor_rounding_logic():
    """
    Verify 'Nerd Economics' for Resistors:
    Buffer +5, then Round UP to nearest 10.
    """
    # Case 1: Need 1. Buffer = 6. Round up -> 10.
    qty, _ = get_buy_details("Resistors", "10k", 1)
    assert qty == 10

    # Case 2: Need 6. Buffer = 11. Round up -> 20.
    qty, _ = get_buy_details("Resistors", "10k", 6)
    assert qty == 20

    # Case 3: Need 15. Buffer = 20. Round up -> 20 (Exact match).
    qty, _ = get_buy_details("Resistors", "10k", 15)
    assert qty == 20


def test_capacitor_material_recommendations():
    """
    Verify MLCC vs Box Film vs Electrolytic logic.
    """
    # Case 1: Pico range (<= 1nF) -> MLCC
    _, note_p = get_buy_details("Capacitors", "100p", 1)
    assert "MLCC" in note_p

    # Case 2: Nano range (> 1nF, < 1uF) -> Box Film
    _, note_n = get_buy_details("Capacitors", "100n", 1)
    assert "Box Film" in note_n
    assert "Electrolytic" not in note_n

    # Case 3: 1uF Crossover -> Box Film + Warning
    _, note_1u = get_buy_details("Capacitors", "1u", 1)
    assert "Box Film" in note_1u
    assert "Check BOM" in note_1u

    # Case 4: Bulk range (> 1uF) -> Electrolytic
    _, note_bulk = get_buy_details("Capacitors", "100u", 1)
    assert "Electrolytic" in note_bulk


def test_hardware_injection_and_smart_merge():
    """
    Verify get_standard_hardware calculates standard items
    AND merges them into inventory if they already exist.
    """
    # Setup: Inventory has 2 existing 3.3k resistors (for the circuit)
    # and 3 Pots (which implies we need 3 Knobs)
    inventory = {"Resistors | 3.3k": 2, "Potentiometers | 100k-B": 3}

    # Run injection for 1 pedal
    hardware_list = get_standard_hardware(inventory, pedal_count=1)

    # CHECK 1: Smart Merge
    # The function should have found "Resistors | 3.3k" and incremented it by 1 (for the LED).
    # It should NOT be in the hardware_list returned.
    assert inventory["Resistors | 3.3k"] == 3  # 2 original + 1 injected
    assert not any(
        item["Part"] == "Resistor 3.3k (Metal Film)" for item in hardware_list
    )

    # CHECK 2: Forced Injection
    # Enclosures should always be there
    enclosures = [x for x in hardware_list if "Enclosure" in x["Part"]]
    assert len(enclosures) == 1
    assert enclosures[0]["Buy Qty"] == 1

    # CHECK 3: Dynamic Knob Count
    # 3 Pots -> 3 Knobs
    knobs = [x for x in hardware_list if x["Part"] == "Knob"]
    assert len(knobs) == 1
    assert knobs[0]["Buy Qty"] == 3
