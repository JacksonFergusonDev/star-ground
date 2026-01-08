import pytest
from collections import defaultdict
from typing import cast
from hypothesis import given, strategies as st
from src.presets import BOM_PRESETS
from src.bom_lib import (
    InventoryType,
    parse_with_verification,
    get_buy_details,
    parse_value_to_float,
    float_to_search_string,
    float_to_display_string,
    get_standard_hardware,
    get_spec_type,
    generate_search_term,
    generate_tayda_url,
    expand_refs,
    parse_user_inventory,
    calculate_net_needs,
    deduplicate_refs,
)

# Standard Unit Tests


def test_basic_resistor_parsing():
    """Does it handle a perfect input?"""
    raw_text = "R1 10k"
    inventory, stats = parse_with_verification([raw_text], source_name="Test Bench")

    assert inventory["Resistors | 10k"]["qty"] == 1
    assert "R1" in inventory["Resistors | 10k"]["refs"]
    assert "R1" in inventory["Resistors | 10k"]["sources"]["Test Bench"]

    assert stats["parts_found"] == 1
    assert len(stats["residuals"]) == 0


def test_source_tracking_logic():
    """
    Verify that we can distinguish WHERE a part came from.
    """
    raw_text = "R1 10k"
    inventory, _ = parse_with_verification([raw_text], source_name="Big Muff")

    # Simulate a merge (manually adding a second source)
    inventory["Resistors | 10k"]["qty"] += 1
    inventory["Resistors | 10k"]["sources"]["Tube Screamer"].append("R5")

    item = inventory["Resistors | 10k"]
    assert item["qty"] == 2
    assert item["sources"]["Big Muff"] == ["R1"]
    assert item["sources"]["Tube Screamer"] == ["R5"]


def test_pcb_trap():
    """Does it handle the permissive PCB logic (PCB anywhere in line)?"""
    # Parser now expects "PCB" to appear anywhere in the line
    raw_text = "BIG MUFF DIY PCB GUITAR EFFECT"
    inventory, stats = parse_with_verification([raw_text], source_name="My Build")

    key = "PCB | BIG MUFF DIY PCB GUITAR EFFECT"
    assert inventory[key]["qty"] == 1
    assert "PCB" in inventory[key]["sources"]["My Build"]


def test_2n5457_behavior():
    """
    Ensure we do NOT auto-replace 2N5457 or inject adapters.
    The user is smart; we just warn them in the notes.
    """
    # Case 1: Vintage THT Part
    raw_text = "Q1 2N5457"
    inventory, _ = parse_with_verification([raw_text])

    # Should stay as 2N5457
    assert inventory["Transistors | 2N5457"]["qty"] == 1
    # Should NOT inject adapter
    assert inventory.get("Hardware/Misc | SMD_ADAPTER_BOARD", {}).get("qty", 0) == 0

    # Case 2: Modern SMD Part
    raw_text_2 = "Q2 MMBF5457"
    inventory_2, _ = parse_with_verification([raw_text_2])

    # Should stay as MMBF5457
    assert inventory_2["Transistors | MMBF5457"]["qty"] == 1
    # Should NOT inject adapter (User might have SOT-23 pads)
    assert inventory_2.get("Hardware/Misc | SMD_ADAPTER_BOARD", {}).get("qty", 0) == 0


def test_warning_flags():
    """Verify that get_buy_details generates the correct warnings."""
    # Test Obsolete Warning
    _, note = get_buy_details("Transistors", "2N5457", 1)
    assert "Obsolete" in note

    # Test SMD Warning
    _, note = get_buy_details("Transistors", "MMBF5457", 1)
    assert "SMD Part" in note


# Stress Testing


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
    # Case 1: Pico range (<= 1nF) -> Class 1 Ceramic (C0G)
    _, note_p = get_buy_details("Capacitors", "100p", 1)
    assert "Class 1 Ceramic" in note_p

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
    inventory = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    inventory["Resistors | 3.3k"]["qty"] = 2
    inventory["Potentiometers | 100k-B"]["qty"] = 3

    # Run injection for 1 pedal (Mutates in-place)
    get_standard_hardware(inventory, pedal_count=1)

    # CHECK 1: Smart Merge
    # The function should have found "Resistors | 3.3k" and incremented it by 1 (for the LED).
    assert inventory["Resistors | 3.3k"]["qty"] == 3  # 2 original + 1 injected

    # Verify the source tag was added
    assert "Auto-Inject" in inventory["Resistors | 3.3k"]["sources"]

    # CHECK 2: Forced Injection
    # Enclosures should be injected directly into inventory
    enc_key = "Hardware/Misc | 1590B Enclosure"
    assert inventory[enc_key]["qty"] == 1
    assert "Auto-Inject" in inventory[enc_key]["sources"]

    # CHECK 3: Dynamic Knob Count
    # 3 Pots -> 3 Knobs injected
    knob_key = "Hardware/Misc | Knob"
    assert inventory[knob_key]["qty"] == 3


# Search Engine & Vendor Integration Tests


def test_spec_type_logic():
    """Verify we correctly identify capacitor types based on value."""
    # Pico range -> MLCC
    assert get_spec_type("Capacitors", "100p") == "MLCC"

    # Nano range -> Box Film
    assert get_spec_type("Capacitors", "10n") == "Box Film"

    # The 1uF Crossover -> Box Film
    assert get_spec_type("Capacitors", "1u") == "Box Film"

    # Bulk range -> Electrolytic
    assert get_spec_type("Capacitors", "100u") == "Electrolytic"


def test_vintage_search_mapping():
    """Verify JRC4558 maps to NJM4558."""
    res = generate_search_term("ICs", "JRC4558")
    assert res == "NJM4558D"


def test_expert_system_recommendations():
    """Verify Silicon Sommelier logic for ICs and Diodes."""
    # 1. IC Mojo (TL072 -> OPA2134)
    _, note_ic = get_buy_details("ICs", "TL072", 1)
    assert "OPA2134" in note_ic
    assert "Hi-Fi" in note_ic

    # 2. Diode Textures (1N4148 -> Tube-like)
    _, note_d = get_buy_details("Diodes", "1N4148", 1)
    assert "1N4001" in note_d
    assert "Tube-like" in note_d


def test_fuzz_germanium_trigger():
    """Verify Fuzz PCBs trigger Germanium Transistor injection."""
    # Setup inventory with a Fuzz PCB
    inventory = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    inventory["PCB | Fuzz Face"]["qty"] = 1

    get_standard_hardware(inventory, pedal_count=1)

    # Check for Ge Transistors in the dictionary
    ge_key = "Transistors | Germanium PNP"
    assert ge_key in inventory
    assert inventory[ge_key]["qty"] == 0

    # Check that the note made it into the source tag
    sources = inventory[ge_key]["sources"]["Auto-Inject"]
    assert any("Vintage Option" in s for s in sources)


def test_search_term_generation():
    """Verify the string building logic for Tayda."""
    # 1. Resistors (Must include keywords)
    res = generate_search_term("Resistors", "10k")
    assert res == "10k ohm 1/4w metal film"

    # 2. Potentiometers (Taper Mapping)
    # Log Taper
    pot_log = generate_search_term("Potentiometers", "100k-A")
    assert "Logarithmic" in pot_log
    assert "100k" in pot_log

    # Linear Taper
    pot_lin = generate_search_term("Potentiometers", "10k-B")
    assert "Linear" in pot_lin

    # 3. Capacitors (Should include spec type)
    cap = generate_search_term("Capacitors", "100n", "Box Film")
    assert cap == "100nF Box Film"

    # 4. Diodes (LED Handling)
    led = generate_search_term("Diodes", "LED")
    assert "3mm" in led


def test_tayda_url_encoding():
    """Does it properly encode spaces and special chars?"""
    term = "10k ohm 1/4w"
    url = generate_tayda_url(term)

    assert "https://www.taydaelectronics.com" in url
    assert "10k+ohm+1%2F4w" in url


def test_hardware_search_term_validity():
    """
    Ensure injected hardware keys generate valid search terms/links.
    (Logic moved from bom_lib to app.py, so we simulate the app's utilization).
    """
    # Fix: Must use defaultdict to prevent KeyError during injection
    inventory = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )

    get_standard_hardware(inventory, pedal_count=1)

    # Grab the Enclosure Key
    target_key = "Hardware/Misc | 1590B Enclosure"
    assert target_key in inventory

    # Simulate App Logic: specific -> generate -> url
    category, val = target_key.split(" | ", 1)

    term = generate_search_term(category, val)
    url = generate_tayda_url(term)

    # Verify content
    assert "1590B Enclosure" in term
    assert "1590B+Enclosure" in url


# Range Expansion Tests


def test_range_expansion_logic():
    """
    Verify the regex logic for expanding R1-R4 (Commit 1).
    """
    # 1. Standard Range
    assert expand_refs("R1-R4") == ["R1", "R2", "R3", "R4"]

    # 2. Mixed Case / No Space
    assert expand_refs("C1-3") == ["C1", "C2", "C3"]

    # 3. Single Item (Pass-through)
    assert expand_refs("U1") == ["U1"]

    # 4. Broken/Weird input (Safety check)
    assert expand_refs("R1-") == ["R1-"]


def test_ref_expansion_integrity():
    """
    Integration Test: Ensure parsers actually USE the expansion (Commit 1).
    """
    raw_text = "R1-R3 10k"
    inventory, _ = parse_with_verification([raw_text], source_name="Range Test")

    item = inventory["Resistors | 10k"]

    # Qty check
    assert item["qty"] == 3

    # Source Integrity Check
    # We want ['R1', 'R2', 'R3'], NOT ['R1-R3']
    assert "R1" in item["sources"]["Range Test"]
    assert "R2" in item["sources"]["Range Test"]
    assert "R3" in item["sources"]["Range Test"]


# Inventory & Logistics Tests


def test_zero_buy_guard():
    """
    Ensure we don't buy parts if Net Need is 0, even if 'Nerd Economics'
    would usually suggest a buffer.
    """
    # Standard logic: 10k -> Buffer +5 -> Round up -> Buy 10.
    # BUT if input is 0, we must buy 0.
    qty, note = get_buy_details("Resistors", "10k", 0)
    assert qty == 0
    assert note == ""

    # Negative input safety check
    qty_neg, _ = get_buy_details("Resistors", "10k", -5)
    assert qty_neg == 0


def test_user_inventory_parsing():
    """
    Verify that we can digest a User Stock CSV and that values
    are normalized to match BOM keys.
    """
    # Mock CSV Content
    csv_content = """Category,Part,Qty
Resistors,10k,100
Capacitors,100n,50
Resistors,1k5,20
"""
    # Create temp file
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".csv") as tmp:
        tmp.write(csv_content)
        tmp_path = tmp.name

    try:
        stock = parse_user_inventory(tmp_path)

        # 1. Check Normalization (1k5 -> 1.5k)
        assert stock["Resistors | 1.5k"]["qty"] == 20

        # 2. Check Basic Ingestion
        assert stock["Resistors | 10k"]["qty"] == 100
        assert stock["Capacitors | 100n"]["qty"] == 50

    finally:
        os.remove(tmp_path)


def test_net_needs_calculation():
    """
    Verify the logic: Net = Max(0, BOM - Stock)
    """
    # 1. Setup BOM
    bom = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    bom["Resistors | 10k"]["qty"] = 10  # Need 10
    bom["Capacitors | 100n"]["qty"] = 5  # Need 5

    # 2. Setup Stock
    stock = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    stock["Resistors | 10k"]["qty"] = 4  # Have 4 (Deficit 6)
    stock["Capacitors | 100n"]["qty"] = 10  # Have 10 (Surplus 5)

    # 3. Calculate
    net_inv = calculate_net_needs(bom, stock)

    # 4. Verify Deficit (10 - 4 = 6)
    assert net_inv["Resistors | 10k"]["qty"] == 6

    # 5. Verify Surplus (5 - 10 = -5 -> Floor at 0)
    assert net_inv["Capacitors | 100n"]["qty"] == 0


def test_preset_integrity():
    """
    Verify that every defined preset is valid, parseable BOM text.
    This catches typos or empty strings in the presets file.
    """
    for name, data in BOM_PRESETS.items():
        # Handle new Dict format vs Legacy string
        if isinstance(data, dict):
            raw_text = data["bom_text"]
        else:
            raw_text = data

        # Sanity check: Text should exist
        assert raw_text.strip(), f"Preset '{name}' is empty!"

        # Parse check
        _, stats = parse_with_verification([raw_text], source_name=name)

        # Must find parts
        assert stats["parts_found"] > 0, f"Preset '{name}' yielded 0 parts!"
        assert stats["lines_read"] > 0


def test_ref_deduplication_and_sorting():
    """
    Verify the Field Manual helper logic (Commit 8).
    """
    # 1. Basic Deduplication
    raw = ["R1", "R1", "R2"]
    assert deduplicate_refs(raw) == ["R1", "R2"]

    # 2. Natural Sorting (The "R10 Problem")
    # ASCII sort would be: R1, R10, R2.
    # Natural sort should be: R1, R2, R10.
    unsorted = ["R10", "R2", "R1"]
    assert deduplicate_refs(unsorted) == ["R1", "R2", "R10"]

    # 3. Complex Mix
    complex_list = ["C2", "C1", "C10", "C2"]
    assert deduplicate_refs(complex_list) == ["C1", "C2", "C10"]

    # 4. Empty Safety
    assert deduplicate_refs([]) == []
