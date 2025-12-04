import pytest
from hypothesis import given, strategies as st
from src.bom_lib import parse_with_verification, get_buy_details

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

def test_smd_injection():
    """Does it force the adapter board for the obsolete JFET?"""
    raw_text = "Q1 2N5457"
    inventory, stats = parse_with_verification([raw_text])
    
    # Check for the part rename
    assert inventory["Transistors | MMBF5457"] == 1
    # Check for the injected adapter
    assert inventory["Hardware/Misc | SMD_ADAPTER_BOARD"] == 1

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