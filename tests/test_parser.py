"""
Unit and Property-Based Tests for the Parser and Core Business Logic.

This suite covers:
1. Basic BOM parsing and normalization.
2. Advanced parsing features (Ranges, PCB detection, exclusions).
3. "Nerd Economics" (purchasing logic and buffers).
4. Component classification and search term generation.
5. Property-based stress testing using Hypothesis.
"""

from collections.abc import MutableMapping
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.bom_lib import (
    BOM_PRESETS,
    BuildHardware,
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
    parse_value_to_decimal,
    parse_with_verification,
)
from src.bom_lib.classifier import normalize_value_to_quantity
from src.bom_lib.enums import ComponentCategory, ComponentSpec
from src.bom_lib.types import ComponentKey, Inventory, PDFPageExtraction

# --- Standard Unit Tests ---


def test_basic_resistor_parsing():
    """
    Verifies the 'Happy Path' for parsing a standard component line.

    Ensures that a simple string like "R1 10k" is correctly parsed into
    the inventory structure with the right quantity and source mapping.
    """
    raw_text = "R1 10k"
    res = parse_with_verification([raw_text], source_name="Test Bench")
    inventory, stats = res.inventory, res.stats

    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    assert inventory[key_r]["qty"] == 1
    assert "R1" in inventory[key_r]["refs"]
    assert "R1" in inventory[key_r]["sources"]["Test Bench"]

    assert stats["parts_found"] == 1
    assert len(stats["residuals"]) == 0


def test_source_tracking_logic():
    """
    Verifies that the parser correctly aggregates sources for a single part.

    When the same part appears in multiple projects (or multiple times in one),
    we must track exactly which project requested which specific references.
    """
    raw_text = "R1 10k"
    inventory = parse_with_verification([raw_text], source_name="Big Muff").inventory

    # Simulate a merge operation (manually adding a second source)
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    inventory[key_r]["qty"] += 1
    inventory[key_r]["sources"]["Tube Screamer"].append("R5")

    item = inventory[key_r]
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
    inventory = parse_with_verification([raw_text], source_name="My Build").inventory

    key = ComponentKey(ComponentCategory.PCB, "BIG MUFF DIY PCB GUITAR EFFECT")
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
    inventory = parse_with_verification([raw_text]).inventory

    # Should stay as 2N5457
    key_2n = ComponentKey(ComponentCategory.TRANSISTORS, "2N5457")
    assert inventory[key_2n]["qty"] == 1
    # Should NOT inject adapter
    key_adapter = ComponentKey(ComponentCategory.HARDWARE_MISC, "SMD_ADAPTER_BOARD")
    adapter = inventory.get(key_adapter)
    assert adapter is None or adapter["qty"] == 0

    # Case 2: Modern SMD Part
    raw_text_2 = "Q2 MMBF5457"
    inventory_2 = parse_with_verification([raw_text_2]).inventory

    # Should stay as MMBF5457
    key_mmbf = ComponentKey(ComponentCategory.TRANSISTORS, "MMBF5457")
    assert inventory_2[key_mmbf]["qty"] == 1
    # Should NOT inject adapter (User might have SOT-23 pads on PCB)
    adapter_2 = inventory_2.get(key_adapter)
    assert adapter_2 is None or adapter_2["qty"] == 0


def test_warning_flags():
    """
    Verifies that `get_buy_details` generates appropriate warning flags.

    Ensures that obsolete parts and SMD components trigger visual warnings
    in the final shopping list.
    """
    # Test Obsolete Warning
    note = get_buy_details(
        ComponentCategory.TRANSISTORS,
        "2N5457",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.TRANSISTORS, "2N5457"),
    ).note
    assert "Obsolete" in note

    # Test SMD Warning
    note = get_buy_details(
        ComponentCategory.TRANSISTORS,
        "MMBF5457",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.TRANSISTORS, "MMBF5457"),
    ).note
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
        res = parse_with_verification([garbage_string])
        inventory, stats = res.inventory, res.stats

        assert isinstance(inventory, MutableMapping)
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
    category = ComponentCategory.RESISTORS
    val = "10k"

    buy_qty = get_buy_details(
        category, val, qty, val_qty=normalize_value_to_quantity(category, val)
    ).buy_qty

    # Invariant: We should never buy FEWER than we need
    assert buy_qty >= qty


def test_value_normalization_lifecycle():
    """
    Verifies the full lifecycle of component value normalization.

    Workflow: Raw String -> Decimal (Backend) -> Display String / Search String.
    Ensures that "1k5" becomes Decimal("1500") and converts back correctly.
    """
    # Test Case: 1.5k Resistor
    val = parse_value_to_decimal("1k5")

    # TYPE GUARD: Tell Mypy "If this is None, crash the test right here"
    assert val is not None

    assert val == Decimal("1500")
    assert float_to_search_string(float(val)) == "1.5k"
    assert float_to_display_string(float(val)) == "1k5"

    # Test Case: 100n (Capacitor Normalization)
    val1 = parse_value_to_decimal("100n")

    # TYPE GUARD
    assert val1 is not None

    # We normalized 100n -> 1.0e-7.
    # Our renderer might output "100n" or "0.1u" depending on formatting rules,
    # but the numeric value must be correct.
    out = float_to_search_string(float(val1))
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
    # Note: 0.1 -> parse_value_to_decimal -> 0.1
    note_r = get_buy_details(
        ComponentCategory.RESISTORS,
        "0.1",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "0.1"),
    ).note
    assert "Suspicious" in note_r
    assert "< 1Ω" in note_r

    # 2. Capacitor too huge (1 Farad)
    # Note: "1F" -> parse_value_to_decimal -> 1.0 (Huge!)
    note_c = get_buy_details(
        ComponentCategory.CAPACITORS,
        "1F",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1F"),
    ).note
    assert "Suspicious" in note_c
    assert "> 10mF" in note_c

    # 3. Normal values should be fine
    note_ok = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    ).note
    assert "Suspicious" not in note_ok


def test_resistor_rounding_logic():
    """
    Verifies 'Nerd Economics' purchasing logic for Resistors.

    Rule: Add a buffer of 5, then round UP to the nearest 10.
    """
    # Case 1: Need 1. Buffer = 6. Round up -> 10.
    qty = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    ).buy_qty
    assert qty == 10

    # Case 2: Need 6. Buffer = 11. Round up -> 20.
    qty = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        6,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    ).buy_qty
    assert qty == 20

    # Case 3: Need 15. Buffer = 20. Round up -> 20 (Exact match).
    qty = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        15,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    ).buy_qty
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
    note_p = get_buy_details(
        ComponentCategory.CAPACITORS,
        "100p",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100p"),
    ).note
    assert "Class 1 Ceramic" in note_p

    # Case 2: Nano range (> 1nF, < 1uF) -> Box Film
    note_n = get_buy_details(
        ComponentCategory.CAPACITORS,
        "100n",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100n"),
    ).note
    assert ComponentSpec.BOX_FILM.value in note_n
    assert ComponentSpec.ELECTROLYTIC.value not in note_n

    # Case 3: 1uF Crossover -> Box Film + Warning
    note_1u = get_buy_details(
        ComponentCategory.CAPACITORS,
        "1u",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1u"),
    ).note
    assert ComponentSpec.BOX_FILM.value in note_1u
    assert "Check BOM" in note_1u

    # Case 4: Bulk range (> 1uF) -> Electrolytic
    note_bulk = get_buy_details(
        ComponentCategory.CAPACITORS,
        "100u",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100u"),
    ).note
    assert ComponentSpec.ELECTROLYTIC.value in note_bulk


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
    inventory = Inventory()
    key_3k3 = ComponentKey(ComponentCategory.RESISTORS, "3.3k")
    key_pot = ComponentKey(ComponentCategory.POTENTIOMETERS, "100k-B")
    inventory[key_3k3]["qty"] = 2
    inventory[key_pot]["qty"] = 3

    # Run injection for 1 pedal (Mutates in-place)
    get_standard_hardware(inventory, [BuildHardware(1)])

    # CHECK 1: Smart Merge
    # The function should have found "Resistors | 3.3k" and incremented it by 1 (for the LED).
    assert inventory[key_3k3]["qty"] == 3  # 2 original + 1 injected

    # Verify the source tag was added
    assert "Auto-Inject" in inventory[key_3k3]["sources"]

    # CHECK 2: Forced Injection
    # Enclosures should be injected directly into inventory
    enc_key = ComponentKey(ComponentCategory.HARDWARE_MISC, "1590B Enclosure")
    assert inventory[enc_key]["qty"] == 1
    assert "Auto-Inject" in inventory[enc_key]["sources"]

    # CHECK 3: Dynamic Knob Count
    # 3 Pots -> 3 Knobs injected
    knob_key = ComponentKey(ComponentCategory.HARDWARE_MISC, "Knob")
    assert inventory[knob_key]["qty"] == 3


# --- Search Engine & Vendor Integration Tests ---


def test_spec_type_logic():
    """Verifies correct capacitor dielectric classification based on value."""
    # Pico range -> MLCC
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "100p",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100p"),
        )
        == ComponentSpec.MLCC
    )

    # Nano range -> Box Film
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "10n",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "10n"),
        )
        == ComponentSpec.BOX_FILM
    )

    # The 1uF Crossover -> Box Film
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "1u",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "1u"),
        )
        == ComponentSpec.BOX_FILM
    )

    # Bulk range -> Electrolytic
    assert (
        get_spec_type(
            ComponentCategory.CAPACITORS,
            "100u",
            val_qty=normalize_value_to_quantity(ComponentCategory.CAPACITORS, "100u"),
        )
        == ComponentSpec.ELECTROLYTIC
    )


def test_vintage_search_mapping():
    """Verifies that generic vintage part numbers map to modern equivalents."""
    res = generate_search_term(ComponentCategory.ICS, "JRC4558")
    assert res == "NJM4558D"


def test_expert_system_recommendations():
    """
    Verifies 'Silicon Sommelier' expert recommendations.

    Ensures that generic parts (TL072, 1N4148) get annotated with
    audiophile-grade alternatives or usage notes.
    """
    # 1. IC Mojo (TL072 -> OPA2134)
    note_ic = get_buy_details(
        ComponentCategory.ICS,
        "TL072",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.ICS, "TL072"),
    ).note
    assert "OPA2134" in note_ic
    assert "Hi-Fi" in note_ic

    # 2. Diode Textures (1N4148 -> Tube-like)
    note_d = get_buy_details(
        ComponentCategory.DIODES,
        "1N4148",
        1,
        val_qty=normalize_value_to_quantity(ComponentCategory.DIODES, "1N4148"),
    ).note
    assert "1N4001" in note_d
    assert "Tube-like" in note_d


def test_fuzz_germanium_trigger():
    """
    Verifies that detecting a 'Fuzz Face' PCB triggers the injection
    of Germanium Transistors into the shopping list.
    """
    # Setup inventory with a Fuzz PCB
    inventory = Inventory()
    pcb_key = ComponentKey(ComponentCategory.PCB, "Fuzz Face")
    inventory[pcb_key]["qty"] = 1

    get_standard_hardware(inventory, [BuildHardware(1)])

    # Check for Ge Transistors in the dictionary
    ge_key = ComponentKey(ComponentCategory.TRANSISTORS, "Germanium PNP")
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
    res = generate_search_term(ComponentCategory.RESISTORS, "10k")
    assert res == "10k ohm 1/4w metal film"

    # 2. Potentiometers (Taper Mapping)
    # Log Taper
    pot_log = generate_search_term(ComponentCategory.POTENTIOMETERS, "100k-A")
    assert "Logarithmic" in pot_log
    assert "100k" in pot_log

    # Linear Taper
    pot_lin = generate_search_term(ComponentCategory.POTENTIOMETERS, "10k-B")
    assert "Linear" in pot_lin

    # 3. Capacitors (Should include spec type)
    cap = generate_search_term(
        ComponentCategory.CAPACITORS, "100n", ComponentSpec.BOX_FILM
    )
    assert cap == "100nF Box Film"

    # 4. Diodes (LED Handling)
    led = generate_search_term(ComponentCategory.DIODES, "LED")
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
    inventory = Inventory()

    get_standard_hardware(inventory, [BuildHardware(1)])

    # Grab the Enclosure Key
    target_key = ComponentKey(ComponentCategory.HARDWARE_MISC, "1590B Enclosure")
    assert target_key in inventory

    term = generate_search_term(target_key.category, target_key.value)
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

    # 5. Comma-separated lists of single refs and ranges (grouped BOMs)
    assert expand_refs("R10-R12,R18") == ["R10", "R11", "R12", "R18"]
    assert expand_refs("C4, C5") == ["C4", "C5"]

    # 6. A spaced dash (6 Band EQ: 'POT1 - 6')
    assert expand_refs("POT1 - 6") == ["POT1", "POT2", "POT3", "POT4", "POT5", "POT6"]

    # 7. A bare number reuses the previous prefix (Q-Tune: 'C12, 15, 16')
    assert expand_refs("C12,15,16") == ["C12", "C15", "C16"]

    # 8. A typeset en dash (Aion FX legacy docs)
    assert expand_refs("Q1\u2013Q2") == ["Q1", "Q2"]


def test_ref_expansion_integrity():
    """
    Integration Test: Verifies that the parser actually invokes expansion logic.
    """
    raw_text = "R1-R3 10k"
    inventory = parse_with_verification([raw_text], source_name="Range Test").inventory

    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    item = inventory[key_r]

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
    rec = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        0,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    )
    assert rec.buy_qty == 0
    assert rec.note == ""

    # Negative input safety check
    rec_neg = get_buy_details(
        ComponentCategory.RESISTORS,
        "10k",
        -5,
        val_qty=normalize_value_to_quantity(ComponentCategory.RESISTORS, "10k"),
    )
    assert rec_neg.buy_qty == 0


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
        k_1k5 = ComponentKey(ComponentCategory.RESISTORS, "1.5k")
        assert stock[k_1k5]["qty"] == 20

        # 2. Check Basic Ingestion
        k_10k = ComponentKey(ComponentCategory.RESISTORS, "10k")
        k_100n = ComponentKey(ComponentCategory.CAPACITORS, "100n")
        assert stock[k_10k]["qty"] == 100
        assert stock[k_100n]["qty"] == 50

    finally:
        os.remove(tmp_path)


def test_net_needs_calculation():
    """
    Verifies the inventory subtraction logic.

    Formula: Net = Max(0, BOM_Needed - Stock_Available)
    """
    # 1. Setup BOM
    key_r = ComponentKey(ComponentCategory.RESISTORS, "10k")
    key_c = ComponentKey(ComponentCategory.CAPACITORS, "100n")

    bom = Inventory()
    bom[key_r]["qty"] = 10  # Need 10
    bom[key_c]["qty"] = 5  # Need 5

    # 2. Setup Stock
    stock = Inventory()
    stock[key_r]["qty"] = 4  # Have 4 (Deficit 6)
    stock[key_c]["qty"] = 10  # Have 10 (Surplus 5)

    # 3. Calculate
    net_inv = calculate_net_needs(bom, stock)

    # 4. Verify Deficit (10 - 4 = 6)
    assert net_inv[key_r]["qty"] == 6

    # 5. Verify Surplus (5 - 10 = -5 -> Floor at 0)
    assert net_inv[key_c]["qty"] == 0


def test_preset_integrity():
    """
    Verifies the integrity of the static `BOM_PRESETS` library.

    Iterates through every preset to ensure:
    1. The data structure is valid.
    2. The text content is not empty.
    3. The parser can successfully find parts in it.
    """
    for name, data in BOM_PRESETS.items():
        val = data["bom_text"]
        raw_text = str(val)

        # Sanity check: Text should exist
        assert raw_text.strip(), f"Preset '{name}' is empty!"

        # Parse check
        stats = parse_with_verification([raw_text], source_name=name).stats

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


def test_a_designator_repeated_in_another_table_is_another_board() -> None:
    # EV Mini: the LFO and Audio PCBs both list Q1 2N5088; a repeat in the same
    # table (Genmaicha's doubled R102 row) is one part.
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _ingest_entries

    entries = [
        Entry("Q1", "2N5088", 1, table=0),
        Entry("R102", "100K", 1, table=0),
        Entry("R102", "100K", 1, table=0),
        Entry("Q1", "2N5088", 3, table=1),
    ]
    inventory, _ = _ingest_entries(entries, "T")
    q1 = inventory[ComponentKey(ComponentCategory.TRANSISTORS, "2N5088")]
    r102 = inventory[ComponentKey(ComponentCategory.RESISTORS, "100k")]
    assert (q1["qty"], r102["qty"]) == (2, 1)


def _typed_page(
    rows: list[tuple[str, str, str]], cols: tuple[float, float, float] = (50, 110, 200)
) -> PDFPageExtraction:
    """One page holding an Aion FX style 'PART | VALUE | TYPE' table."""
    from src.bom_lib import PDFWord

    words = []
    for k, cells in enumerate([("PART", "VALUE", "TYPE"), *rows]):
        for text, x in zip(cells, cols, strict=True):
            for word in text.split():
                words.append(
                    PDFWord(
                        text=word,
                        x0=x,
                        x1=x + 6 * len(word),
                        top=100.0 + 20 * k,
                        size=12.0,
                    )
                )
                x += 6 * len(word) + 2.7
    return PDFPageExtraction(tables=[], text="", words=words)


def test_typed_table_repeated_labels_are_separate_parts() -> None:
    # Aion FX Asteria: 'GAIN | 1MA | ...pot' and 'GAIN | SPDT on-on | Toggle switch'.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import _text_table_entries

    page = _typed_page(
        [
            ("GAIN", "1MA", "16mm right-angle PCB mount pot"),
            ("GAIN", "SPDT", "Toggle switch, SPDT on-on"),
            ("LEDR", "4k7", "Metal film resistor, 1/4W"),
        ]
    )
    stats = create_empty_stats()
    entries, _ = _text_table_entries([page], stats)
    assert [(e.ref, e.value) for e in entries] == [
        ("GAIN", "1MA"),
        ("GAIN (2)", "SPDT ON/ON"),
        ("LEDR", "4k7"),
    ]
    assert stats["warnings"] == []


def test_typed_table_socket_row_keeps_its_label() -> None:
    # Aion FX Amethyst: 'IC1-SOCKET' is the socket, not IC1 named by its role
    # the way GuitarPCB's 'TR1-BIAS' is.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import _text_table_entries

    page = _typed_page(
        [
            ("IC1", "TL072", "Op-amp, dual"),
            ("IC1-SOCKET", "DIP-8 socket", "IC socket, DIP-8"),
        ],
        cols=(50, 130, 220),
    )
    entries, _ = _text_table_entries([page], create_empty_stats())
    assert [e.ref for e in entries] == ["IC1", "IC1-SOCKET"]


def test_listed_sockets_are_not_injected_again() -> None:
    # Aion FX lists 'IC1-S | DIP-8 socket'; the IC must not bring a second one.
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _ingest_entries

    inventory, _ = _ingest_entries(
        [
            Entry("IC1", "TL072", 0, table=0),
            Entry(
                "IC1-S",
                "DIP-8 socket",
                0,
                table=0,
                hint=ComponentCategory.HARDWARE_MISC,
            ),
        ],
        "T",
    )
    assert sorted(ref for data in inventory.values() for ref in data["refs"]) == [
        "IC1",
        "IC1-S",
    ]


@pytest.mark.parametrize(
    ("matrix", "kept"),
    [
        ((1, 0, 0, 1, 0, 0), True),
        ((-1, 0, 0, -1, 0, 0), False),  # upside down: '10k' reads 'k01'
        ((0, 1, -1, 0, 0, 0), False),  # bottom to top: '100uF' reads 'Fu001'
        ((0, -1, 1, 0, 0, 0), True),  # top to bottom reads in order
    ],
)
def test_reversed_text_is_not_read(matrix: tuple[float, ...], kept: bool) -> None:
    from src.bom_lib.parser import _readable

    assert _readable({"object_type": "char", "matrix": matrix}) is kept
    assert _readable({"object_type": "line"}) is True


def test_untyped_grid_reads_named_controls_but_not_hardware() -> None:
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import _untyped_refs

    stats = create_empty_stats()
    assert _untyped_refs(stats, ["VOL"], "A500K") == ["VOL"]
    assert _untyped_refs(stats, ["Screw"], "M3 x 5mm") == []
    assert _untyped_refs(stats, ["R8", "RPDo"], "10k") == ["R8"]  # odd name reported
    assert len(stats["warnings"]) == 1


def test_a_second_board_with_other_values_is_bought_too() -> None:
    # Tweed Fuzztone: the Tweed and Fuzz boards both list R1, with other values;
    # both list D1 1N5817 (bought twice under one designator).
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _ingest_entries

    inventory, stats = _ingest_entries(
        [
            Entry("R1", "1M", 0, table=0),
            Entry("D1", "1N5817", 0, table=0),
            Entry("R1", "1k", 0, table=1),
            Entry("D1", "1N5817", 0, table=1),
        ],
        "T",
    )
    refs = sorted(r for data in inventory.values() for r in data["refs"])
    assert refs == ["D1", "D1", "R1", "R1 (2)"]
    assert [w.ref for w in stats["warnings"]] == ["R1"]


def test_value_column_grid_is_one_variant_per_column() -> None:
    from src.bom_lib.parser import _value_column_grid

    table: list[list[str | None]] = [
        ["", "MUFF Variant", None],
        ["MUFF'N Part #", "Ram", "Triangle"],
        ["R1", "1M", "1M"],
        ["R4", "120R", "jumper"],
        ["R24", "R24 is only available on v3", "omit"],
        ["P1-Sus/Fuzz", "A100k", "B100k"],
        ["R12", "1k8", "CLR"],
        ["SW1", "SPDT", ""],
    ]
    entries = _value_column_grid(table, 0, 0)
    assert entries is not None
    assert [(e.ref, e.value, e.column) for e in entries] == [
        ("R1", "1M", "Ram"),
        ("R1", "1M", "Triangle"),
        ("R4", "120R", "Ram"),
        ("R4", "jumper", "Triangle"),
        ("P1", "A100k", "Ram"),
        ("P1", "B100k", "Triangle"),
        ("R12", "1k8", "Ram"),
        ("R12", "1k8", "Triangle"),  # 'CLR' names the role: same part
        ("SW1", "SPDT", "Ram"),
        ("SW1", "SPDT", "Triangle"),  # blank: same as the first column
    ]
    assert (
        _value_column_grid([["LOCATION", "VALUE", "TYPE"], ["R1", "1M", "R"]], 0, 0)
        is None
    )


def test_near_upright_characters_are_straightened() -> None:
    from types import SimpleNamespace

    from src.bom_lib.parser import _straighten

    char: dict[str, Any] = {
        "upright": False,
        "matrix": (0.66, -3e-8, -4e-8, 0.66, 1.0, 2.0),
        "size": 7.4899,
    }
    _straighten(SimpleNamespace(chars=[char]))  # type: ignore[arg-type]
    assert char["upright"] is True
    assert char["matrix"][:4] == (0.66, 0.0, 0.0, 0.66)
    assert char["size"] == 7.49


def _ruled_page(
    table: list[list[str | None]], lines: list[tuple[str, ...]]
) -> PDFPageExtraction:
    """One page with a ruled table and the words of its rows."""
    from src.bom_lib import PDFWord

    words = []
    for k, cells in enumerate(lines):
        x = 50.0
        for text in cells:
            for word in text.split():
                words.append(
                    PDFWord(
                        text=word,
                        x0=x,
                        x1=x + 6 * len(word),
                        top=100.0 + 20 * k,
                        size=12.0,
                    )
                )
                x += 6 * len(word) + 2.7
            x += 20.0
    return PDFPageExtraction(tables=[table], text="", words=words)


def test_listings_beside_a_ruled_row_are_read() -> None:
    # NostalgiTone: the table reads 'R2 | 1M' but merges the shaded row into
    # 'R1 1M', so C1, C2 and R1 are left to the layout reader.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import read_pages

    page = _ruled_page(
        [["R1 1M", None, "C1 47n", None], ["R2", "1M", "C2", "220n"]],
        [("R1", "1M", "C1", "47n"), ("R2", "1M", "C2", "220n")],
    )
    inventory, _, _ = read_pages([page], "Shaded", create_empty_stats())
    refs = {ref for data in inventory.values() for ref in data["refs"]}
    assert refs == {"R1", "R2", "C1", "C2"}


def test_a_ruled_rows_value_is_not_read_as_a_listing() -> None:
    # Bad Mamma Jamma: 'D1 | LED | 3mm Red LED' is not a bare 'LED' listing.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import read_pages

    page = _ruled_page([["D1", "LED", "3mm Red LED"]], [("D1", "LED", "3mm Red LED")])
    inventory, _, _ = read_pages([page], "Ruled", create_empty_stats())
    refs = {ref for data in inventory.values() for ref in data["refs"]}
    assert refs == {"D1"}


def test_pages_of_tables_are_all_read() -> None:
    # Five Cats Tone Monarch (OCR): the main table has its header, the
    # daughterboard's on the next page does not.
    from src.bom_lib import PDFWord, create_empty_stats
    from src.bom_lib.parser import read_pages

    main = _typed_page(
        [
            ("R1", "10k", "1/4 Watt Resistor"),
            ("R2", "22k", "1/4 Watt Resistor"),
            ("C1", "100n", "Box Cap"),
        ]
    )
    words = [
        PDFWord(text=t, x0=x, x1=x + 6 * len(t), top=100.0 + 20 * k, size=12.0)
        for k, (ref, value) in enumerate(
            [("R23", "47K"), ("R24", "4.7K"), ("D13", "1N5817")]
        )
        for t, x in ((ref, 50.0), (value, 110.0))
    ]
    board = PDFPageExtraction(tables=[], text="", words=words)

    def refs(**kwargs: bool) -> set[str]:
        inventory, _, _ = read_pages([main, board], "x", create_empty_stats(), **kwargs)
        return {r for data in inventory.values() for r in data["refs"]}

    assert "R23" not in refs()
    assert {"R1", "C1", "R23", "D13"} <= refs(tables_only=True)


@pytest.mark.parametrize(
    ("texts", "misread"),
    [
        # Five Cats Funk Machine: another program's OCR drawn over the scan.
        (["Rl", "470K", "RS", "lOK", "Ql", "2N5088", "Ul", "SOnF", "lOuF"], True),
        # A real page may name a part 'RS' or 'DSS', but prints values right.
        (["RS", "DSS", "ROG", "10K", "100nF", "R1", "1M"], False),
        (["Rl", "lOK"], False),
    ],
)
def test_misread_text_layer(texts: list[str], misread: bool) -> None:
    from src.bom_lib import PDFWord
    from src.bom_lib.parser import _misread

    words = [
        PDFWord(text=t, x0=10.0 * k, x1=10.0 * k + 8, top=100.0, size=10.0)
        for k, t in enumerate(texts)
    ]
    assert _misread(words) is misread


def test_control_label_is_no_designator_and_value() -> None:
    # GuitarPCB DumbleTone labels 'Bias Trim'; ShoNuff's 'C1 100n' is a
    # designator and its value run together.
    from src.bom_lib.parser import _CONTROL_LABEL

    assert _CONTROL_LABEL.fullmatch("Bias Trim")
    assert _CONTROL_LABEL.fullmatch("GAIN 2")
    assert not _CONTROL_LABEL.fullmatch("C1 100n")
    assert not _CONTROL_LABEL.fullmatch("P1-Crackle C5k")


def test_same_value_ignores_zero_width_spaces() -> None:
    # GuitarPCB The Vibe: a ruled cell '​ ​ 2N4125 - PNP' and the
    # text reading of the same row.
    from src.bom_lib.parser import _same_value

    assert _same_value("​ ​ 2N4125 - PNP", "2N4125 - PNP")
    assert not _same_value("2N4125 - PNP", "2N3906")


def test_wrapped_ruled_value_matches_its_first_line() -> None:
    # GuitarPCB EMEXAR: the ruled cell 'Bi-color CA LED' reads 'Bi-color CA'
    # as text; another value is another row.
    from src.bom_lib.parser import _wrapped

    assert _wrapped("Bi-color CA LED", "Bi-color CA")
    assert _wrapped("Bi-Color CA LED", "LED")
    assert _wrapped("Bi-Color CA LED", "text)")
    assert not _wrapped("Bi-color CA LED", "Red LED")
    assert not _wrapped("100k", "")


def test_comments_rating_and_pin_voltages() -> None:
    # GuitarPCB Fuzzy Bee: 'C1 | 100n | Box Capacitor, 16+V'.
    from src.bom_lib.parser import _is_ignored, capacitor_notes

    assert capacitor_notes("C1", "100n", "Box Capacitor, 16+V") == "100n (16V minimum)"
    # ColorTone Overdrive's voltage table: 'Q1 | E-.39, B- .94, c- 1.62'.
    assert _is_ignored("E-.39, B- .94, c- 1.62")
    assert not _is_ignored("BC109")


def _words_page(
    rows: list[tuple[tuple[str, float], ...]], text: str = ""
) -> PDFPageExtraction:
    """A page of positioned cells: each row is (text, x) pairs, one line each."""
    from src.bom_lib import PDFWord

    words = []
    for k, cells in enumerate(rows):
        for cell, x in cells:
            for word in cell.split():
                words.append(
                    PDFWord(
                        text=word,
                        x0=x,
                        x1=x + 6 * len(word),
                        top=100.0 + 20 * k,
                        size=12.0,
                    )
                )
                x += 6 * len(word) + 2.7
    return PDFPageExtraction(tables=[], text=text, words=words)


def test_value_column_per_build_makes_builds() -> None:
    # Aion FX Aphelion: 'PART | VALUE (D+) | VALUE (250) | TYPE'.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.builds import plan_builds
    from src.bom_lib.parser import _text_table_entries

    cols = (50.0, 110.0, 190.0, 270.0)

    def row(*cells: str) -> tuple[tuple[str, float], ...]:
        return tuple((c, x) for c, x in zip(cells, cols, strict=True) if c)

    page = _words_page(
        [
            row("PART", "VALUE (D+)", "VALUE (250)", "TYPE"),
            row("R2", "1M", "470k", "Metal film resistor, 1/4W"),
            row("R4", "4k7", "4k7", "Metal film resistor, 1/4W"),
            # Elite prints a hardware row's one value under the first build only.
            row("FSW", "3PDT", "", "Stomp switch, 3PDT"),
        ]
    )
    entries, _ = _text_table_entries([page], create_empty_stats())
    assert [(e.ref, e.value, e.column) for e in entries] == [
        ("R2", "1M", "D+"),
        ("R2", "470k", "250"),
        ("R4", "4k7", "D+"),
        ("R4", "4k7", "250"),
        ("FSW", "3PDT", "D+"),
        ("FSW", "3PDT", "250"),
    ]
    plan = plan_builds(entries, [])
    assert [v.name for v in plan.variants] == ["D+", "250"]


@pytest.mark.parametrize(
    ("ref", "value", "expected"),
    [
        ("C4", "470n / 68n", [("C4", "470n", "vintage"), ("C4", "68n", "modern")]),
        (
            "R11",
            "27k / (omit)",
            [("R11", "27k", "vintage"), ("R11", "(omit)", "modern")],
        ),
        ("IC1 (vintage)", "TA7136P", [("IC1", "TA7136P", "vintage")]),
        ("D6", "1N914 (modern only)", [("D6", "1N914", "modern")]),
        ("R1", "1k", [("R1", "1k", None)]),
    ],
)
def test_two_build_list_rows_split_by_build(
    ref: str, value: str, expected: list[tuple[str, str, str | None]]
) -> None:
    # Aion FX legacy Comet: 'Parts (vintage / modern values)'.
    from src.bom_lib.parser import _version_split

    assert _version_split(ref, value, ("vintage", "modern")) == expected
    assert _version_split(ref, value, None) == [(ref, value, None)]


def test_alternate_spec_table_adds_no_parts() -> None:
    # Aion FX legacy Refractor: 'Silver Pony specs' restate listed values.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _alternate_spec_table

    listed = [
        Entry("R10", "2k", 0, table=0),
        Entry("R17", "27k", 0, table=0),
        Entry("R21", "1k8", 0, table=0),
        Entry("R23", "4k7", 0, table=0),
        Entry("C13", "820pF MLCC", 0, table=0),
    ]
    table: list[list[str | None]] = [
        ["R10", "2k", "47R", "More gain"],
        ["R17", "27k", "10k", "Increased volume"],
        ["R21, R23", "1k8 / 4k7", "4k7 / 1k8", "Tone range"],
        ["C13", "820pF", "820pF", "Same"],
    ]
    stats = create_empty_stats()
    assert _alternate_spec_table(table, listed, stats)
    assert sorted(w.ref for w in stats["warnings"]) == ["R10", "R17", "R21", "R23"]
    # A table of new parts is read as usual.
    assert not _alternate_spec_table(
        [["R30", "1k", "2k", "x"], ["R31", "1k", "2k", "x"], ["R32", "1k", "2k", "x"]],
        listed,
        create_empty_stats(),
    )


def test_shaded_grid_is_read_pair_by_pair() -> None:
    # GuitarPCB SWAH V4: a headerless grid whose shaded rows merge 'R8 6k8';
    # NostalgiTone 70s: '** Q2-Q6 | J201 (see notes)', 'D1 - D2', '* CLR x3';
    # DVF v3: a rating in its own cell.
    from src.bom_lib.parser import _shaded_pairs

    table: list[list[str | None]] = [
        ["R8 6k8", None, "R20", "22k", "C12", "100n", "D6 CA Bi-colour", None],
        ["R9", "4k7", "R21 10k", None, "C1", "22n", "P1-Reso", "B50k Lin"],
        ["R10 6k8", None, "C4", "47n", "63V", "TR1-Bias 50k", None, None],
        [
            "** Q2-Q6",
            "J201 (see notes)",
            "D1 - D2",
            "LED",
            "* CLR x3",
            "1k8-4k7",
            "",
            "",
        ],
    ]
    assert _shaded_pairs(table) == [
        ["R8", "6k8"],
        ["R20", "22k"],
        ["C12", "100n"],
        ["D6", "CA Bi-colour"],
        ["R9", "4k7"],
        ["R21", "10k"],
        ["C1", "22n"],
        ["P1", "B50k Lin"],
        ["R10", "6k8"],
        ["C4", "47n 63V"],
        ["TR1", "50k"],
        ["Q2-Q6", "J201 (see notes)"],
        ["D1-D2", "LED"],
        ["CLR", "1k8-4k7"],
    ]
    # A narrow table is left to the usual reading.
    assert _shaded_pairs([["R1", "1M"], ["R2", "2M"]]) is None


def test_mod_change_list_is_not_parts() -> None:
    # Aion FX legacy Comet's 'Mod 1 | Mod 2' tables re-list IC1, C1, C2...
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _is_change_list

    mods = [Entry("IC1", "OPA2134", 2), Entry("C1", "100n", 2), Entry("C2", "100n", 2)]
    seen = {"IC1", "C1", "C2", "R1"}
    assert _is_change_list(mods, seen, "Modifications\nMod 1 Mod 2\nIC1 OPA2134")
    assert not _is_change_list(mods, seen, "Parts (cont.)")
    assert not _is_change_list([*mods, Entry("R40", "1k", 2)], seen, "Mod 1 Mod 2")


def test_tantalum_note_about_another_part_is_not_this_part() -> None:
    # Aion FX Dryad: C5 '1uF | Film capacitor | Can omit and use 4.7uF tantalum for C6'.
    from src.bom_lib.parser import enrich_value_from_type

    assert (
        enrich_value_from_type(
            "1uF",
            "Film capacitor, 7.2 x 3.5mm",
            "Can omit and use 4.7uF tantalum for C6.",
        )
        == "1uF"
    )
    assert (
        enrich_value_from_type("1uF", "Film capacitor", "Tantalum.") == "1uF Tantalum"
    )


def test_socket_value_outranks_a_wrong_type() -> None:
    # Aion FX Nysiad Silicon: 'IC1-S | DIP-8 socket | BJT transistor, NPN, TO-92'.
    from src.bom_lib import create_empty_stats
    from src.bom_lib.parser import _typed_row

    _, hint = _typed_row(
        create_empty_stats(), "IC1-S", "DIP-8 socket", "BJT transistor, NPN, TO-92", ""
    )
    assert hint == ComponentCategory.HARDWARE_MISC


def test_a_named_label_used_twice_is_two_parts() -> None:
    # Aion FX legacy L5 Preamp lists 'COMP  5MM LED' and 'Comp  SPDT'.
    from src.bom_lib.builds import Entry
    from src.bom_lib.parser import _ingest_entries

    inventory, stats = _ingest_entries(
        [Entry("COMP", "5MM LED", 0), Entry("Comp", "SPDT", 0)], "T"
    )
    refs = sorted(ref for data in inventory.values() for ref in data["refs"])
    assert refs == ["COMP", "Comp (2)"]
    assert stats["warnings"] == []
