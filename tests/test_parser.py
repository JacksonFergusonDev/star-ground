"""
Unit and Property-Based Tests for the Parser and Core Business Logic.

This suite covers:
1. Basic BOM parsing and normalization.
2. Advanced parsing features (Ranges, PCB detection, exclusions).
3. "Nerd Economics" (purchasing logic and buffers).
4. Component classification and search term generation.
5. Property-based stress testing using Hypothesis.
"""

from collections import defaultdict
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.bom_lib import (
    BOM_PRESETS,
    InventoryType,
    calculate_net_needs,
    deduplicate_refs,
    expand_refs,
    float_to_display_string,
    float_to_search_string,
    generate_search_term,
    generate_tayda_url,
    get_buy_details,
    get_spec_type,
    get_standard_hardware,
    parse_user_inventory,
    parse_value_to_float,
    parse_with_verification,
)

# --- Standard Unit Tests ---


def test_basic_resistor_parsing():
    """
    Verifies the 'Happy Path' for parsing a standard component line.

    Ensures that a simple string like "R1 10k" is correctly parsed into
    the inventory structure with the right quantity and source mapping.
    """
    raw_text = "R1 10k"
    inventory, stats = parse_with_verification([raw_text], source_name="Test Bench")

    assert inventory["Resistors | 10k"]["qty"] == 1
    assert "R1" in inventory["Resistors | 10k"]["refs"]
    assert "R1" in inventory["Resistors | 10k"]["sources"]["Test Bench"]

    assert stats["parts_found"] == 1
    assert len(stats["residuals"]) == 0


def test_source_tracking_logic():
    """
    Verifies that the parser correctly aggregates sources for a single part.

    When the same part appears in multiple projects (or multiple times in one),
    we must track exactly which project requested which specific references.
    """
    raw_text = "R1 10k"
    inventory, _ = parse_with_verification([raw_text], source_name="Big Muff")

    # Simulate a merge operation (manually adding a second source)
    inventory["Resistors | 10k"]["qty"] += 1
    inventory["Resistors | 10k"]["sources"]["Tube Screamer"].append("R5")

    item = inventory["Resistors | 10k"]
    assert item["qty"] == 2
    assert item["sources"]["Big Muff"] == ["R1"]
    assert item["sources"]["Tube Screamer"] == ["R5"]


def test_pcb_trap():
    """
    Verifies the relaxed PCB detection logic.

    The parser should identify a line as a PCB if the keyword "PCB" appears
    anywhere in the text, handling permissive formatting (e.g., project titles).
    """
    raw_text = "BIG MUFF DIY PCB GUITAR EFFECT"
    inventory, stats = parse_with_verification([raw_text], source_name="My Build")

    key = "PCB | BIG MUFF DIY PCB GUITAR EFFECT"
    assert inventory[key]["qty"] == 1
    assert "PCB" in inventory[key]["sources"]["My Build"]


def test_2n5457_behavior():
    """
    Verifies specific handling of the 2N5457 JFET logic.

    Legacy THT parts (2N5457) should NOT be auto-replaced, nor should adapters
    be injected unless explicitly requested. We assume the user might have
    actual vintage stock or specifically wants the THT version.
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
    # Should NOT inject adapter (User might have SOT-23 pads on PCB)
    assert inventory_2.get("Hardware/Misc | SMD_ADAPTER_BOARD", {}).get("qty", 0) == 0


def test_warning_flags():
    """
    Verifies that `get_buy_details` generates appropriate warning flags.

    Ensures that obsolete parts and SMD components trigger visual warnings
    in the final shopping list.
    """
    # Test Obsolete Warning
    _, note = get_buy_details("Transistors", "2N5457", 1)
    assert "Obsolete" in note

    # Test SMD Warning
    _, note = get_buy_details("Transistors", "MMBF5457", 1)
    assert "SMD Part" in note


# --- Stress Testing ---


@given(st.text())
def test_parser_never_crashes(garbage_string):
    """
    Hypothesis Stress Test: Fuzzing the parser input.

    Feeds the parser absolute garbage (emojis, chinese characters, binary data,
    massive strings) to ensure it handles exceptions gracefully and NEVER crashes
    the application logic.
    """
    try:
        inventory, stats = parse_with_verification([garbage_string])

        assert isinstance(inventory, dict)
        assert isinstance(stats, dict)

    except Exception as e:
        # If the parser crashes, fail the test and print the input that killed it
        pytest.fail(f"Parser crashed on input: {garbage_string!r} with error: {e}")


@given(st.integers(min_value=1, max_value=1000))
def test_buy_logic_scaling(qty):
    """
    Hypothesis Property Test: Quantity Scaling Integrity.

    Verifies the invariant that 'Buy Qty' must ALWAYS be >= 'BOM Qty',
    regardless of the scale (from 1 to 1000 items).
    """
    category = "Resistors"
    val = "10k"

    buy_qty, note = get_buy_details(category, val, qty)

    # Invariant: We should never buy FEWER than we need
    assert buy_qty >= qty


def test_float_engine_round_trip():
    """
    Verifies the full lifecycle of component value normalization.

    Workflow: Raw String -> Float (Backend) -> Display String / Search String.
    Ensures that "1k5" becomes 1500.0 and converts back correctly.
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
    # Our renderer might output "100n" or "0.1u" depending on formatting rules,
    # but the numeric value must be correct.
    out = float_to_search_string(val1)
    assert "u" in out or "n" in out


def test_bs1852_formatting():
    """
    Verifies the 'BS 1852' (European) formatting style.

    Ensures decimal points are replaced by unit multipliers (e.g., 4.7k -> 4k7)
    to prevent misreading dirty prints.
    """
    val = 1500.0  # 1.5k
    assert float_to_display_string(val) == "1k5"

    val = 2200000.0  # 2.2M
    assert float_to_display_string(val) == "2M2"

    val = 4700.0  # 4.7k
    assert float_to_display_string(val) == "4k7"


def test_suspicious_physics_warnings():
    """
    Verifies validation against physical reality.

    Ensures the system flags values that are likely typos because they are
    physically improbable (e.g., a 1 Farad capacitor or 0.1 Ohm resistor).
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
    Verifies 'Nerd Economics' purchasing logic for Resistors.

    Rule: Add a buffer of 5, then round UP to the nearest 10.
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
    Verifies material suggestions based on capacitance range.

    Logic:
    - Pico (<= 1nF): Class 1 Ceramic (C0G/NP0)
    - Nano (> 1nF, < 1uF): Box Film
    - Bulk (> 1uF): Electrolytic
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
    Verifies that `get_standard_hardware` correctly injects standard parts
    and merges them into existing inventory.

    Scenario:
    - Inventory has 2x 3.3k resistors.
    - Hardware injection adds 1x 3.3k (for LED CLR).
    - Result: 3x 3.3k total, with source tags updated.
    - Also validates dynamic knob calculation based on Potentiometer count.
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


# --- Search Engine & Vendor Integration Tests ---


def test_spec_type_logic():
    """Verifies correct capacitor dielectric classification based on value."""
    # Pico range -> MLCC
    assert get_spec_type("Capacitors", "100p") == "MLCC"

    # Nano range -> Box Film
    assert get_spec_type("Capacitors", "10n") == "Box Film"

    # The 1uF Crossover -> Box Film
    assert get_spec_type("Capacitors", "1u") == "Box Film"

    # Bulk range -> Electrolytic
    assert get_spec_type("Capacitors", "100u") == "Electrolytic"


def test_vintage_search_mapping():
    """Verifies that generic vintage part numbers map to modern equivalents."""
    res = generate_search_term("ICs", "JRC4558")
    assert res == "NJM4558D"


def test_expert_system_recommendations():
    """
    Verifies 'Silicon Sommelier' expert recommendations.

    Ensures that generic parts (TL072, 1N4148) get annotated with
    audiophile-grade alternatives or usage notes.
    """
    # 1. IC Mojo (TL072 -> OPA2134)
    _, note_ic = get_buy_details("ICs", "TL072", 1)
    assert "OPA2134" in note_ic
    assert "Hi-Fi" in note_ic

    # 2. Diode Textures (1N4148 -> Tube-like)
    _, note_d = get_buy_details("Diodes", "1N4148", 1)
    assert "1N4001" in note_d
    assert "Tube-like" in note_d


def test_fuzz_germanium_trigger():
    """
    Verifies that detecting a 'Fuzz Face' PCB triggers the injection
    of Germanium Transistors into the shopping list.
    """
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
    """
    Verifies that search strings are constructed correctly for Tayda Electronics.
    """
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
    """Verifies correct URL encoding of search terms (spaces -> +, / -> %2F)."""
    term = "10k ohm 1/4w"
    url = generate_tayda_url(term)

    assert "https://www.taydaelectronics.com" in url
    assert "10k+ohm+1%2F4w" in url


def test_hardware_search_term_validity():
    """
    Verifies that auto-injected hardware keys generate valid search terms/links.

    (Simulates usage pattern in app.py).
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


# --- Range Expansion Tests ---


def test_range_expansion_logic():
    """
    Verifies the regex logic for expanding component ranges.
    e.g., 'R1-R4' -> ['R1', 'R2', 'R3', 'R4'].
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
    Integration Test: Verifies that the parser actually invokes expansion logic.
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


# --- Inventory & Logistics Tests ---


def test_zero_buy_guard():
    """
    Verifies boundary conditions for purchasing logic.
    If 'Net Need' is 0, 'Buy Qty' must be 0, regardless of standard buffers.
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
    Integration Test: Verifies ingestion of a User Stock CSV.

    Ensures that values from the CSV are normalized (1k5 -> 1.5k) to match
    the canonical format used by BOM parsers.
    """
    # Mock CSV Content
    csv_content = """Category,Part,Qty
Resistors,10k,100
Capacitors,100n,50
Resistors,1k5,20
"""
    # Create temp file
    import os
    import tempfile

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
    Verifies the inventory subtraction logic.

    Formula: Net = Max(0, BOM_Needed - Stock_Available)
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
    Verifies the integrity of the static `BOM_PRESETS` library.

    Iterates through every preset to ensure:
    1. The data structure is valid.
    2. The text content is not empty.
    3. The parser can successfully find parts in it.
    """
    for name, data in BOM_PRESETS.items():
        # Handle new Dict format vs Legacy string
        raw_text = data["bom_text"] if isinstance(data, dict) else data

        # Sanity check: Text should exist
        assert raw_text.strip(), f"Preset '{name}' is empty!"

        # Parse check
        _, stats = parse_with_verification([raw_text], source_name=name)

        # Must find parts
        assert stats["parts_found"] > 0, f"Preset '{name}' yielded 0 parts!"
        assert stats["lines_read"] > 0


def test_ref_deduplication_and_sorting():
    """
    Verifies the logic for natural sorting and deduplication of component references.

    Ensures that ["R1", "R10", "R2"] sorts as ["R1", "R2", "R10"] (Human readable)
    rather than ["R1", "R10", "R2"] (ASCII/Machine sort).
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
