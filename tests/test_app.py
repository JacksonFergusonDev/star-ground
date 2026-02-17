from collections import defaultdict
from typing import cast

import pytest
from streamlit.testing.v1 import AppTest

from src.bom_lib import BOM_PRESETS, InventoryType, ProjectSlot


# --- Helpers ---
class MockFile:
    """
    Fake file object used to simulate file uploads in tests.

    Attributes:
        name (str): The filename.
        content (bytes): The file content encoded as bytes.
    """

    def __init__(self, name, content):
        self.name = name
        self.content = content.encode("utf-8")

    def getvalue(self):
        """Returns the byte content of the mock file."""
        return self.content


# --- Fixtures ---
@pytest.fixture
def app():
    """
    Pytest fixture to initialize and run the Streamlit AppTest runner.

    Sets a higher timeout to accommodate PDF/ZIP generation logic during tests.
    """
    # Increase timeout to allow for heavier processing (PDF generation)
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    return at


# --- Tests ---
def test_smoke_check(app):
    """
    Verifies that the app loads without exceptions and renders the correct title.
    """
    assert not app.exception
    assert app.title[0].value == "⚡ Star Ground"


def test_happy_path_text_paste(app):
    """
    Verifies the standard user flow: Paste Text -> Generate -> Verify Results.
    """
    bom_input = app.text_area[0]
    raw_bom = "R1 10k\nC1 100n\nU1 TL072"
    bom_input.set_value(raw_bom).run()

    # Click "Generate Master List" (Button index 1 in the UI hierarchy)
    app.button[1].click().run()

    assert not app.exception
    # Verify the metric shows 3 parts found
    assert app.metric[1].value == "3"

    # Verify the dataframe contains the expected part
    df = app.dataframe[0].value
    assert "TL072" in df["Part"].values


def test_csv_processing_via_state_injection(app):
    """
    Verifies the integration between the data layer and the UI visualization.

    This test bypasses the Streamlit FileUploader (which is difficult to mock
    in the test runner) by injecting a pre-parsed inventory directly into
    session_state. It ensures that if data *is* loaded, the UI reacts correctly.
    """
    # 1. Mock the inventory structure that the CSV parser WOULD have produced
    mock_inventory = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    mock_inventory["Resistors | 10k"]["qty"] = 5
    mock_inventory["Resistors | 10k"]["sources"]["Mock Project"] = ["R1-R5"]

    mock_inventory["Capacitors | 22n"]["qty"] = 2
    mock_inventory["Capacitors | 22n"]["sources"]["Mock Project"] = ["C1", "C2"]

    # Mock Stock (User already has 2x 10k resistors)
    mock_stock = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    mock_stock["Resistors | 10k"]["qty"] = 2

    mock_stats = {"lines_read": 7, "parts_found": 7, "residuals": []}

    # 2. Inject into session state
    app.session_state["inventory"] = mock_inventory
    app.session_state["stock"] = mock_stock  # Inject Stock to trigger "Net Need" logic
    app.session_state["stats"] = mock_stats

    # Ensure at least one slot exists to prevent UI errors
    if "pedal_slots" not in app.session_state:
        app.session_state["pedal_slots"] = [
            {"id": "test", "name": "test", "method": "Paste Text"}
        ]

    # 3. Rerun the app to trigger the "Main Process" block with injected state
    app.run()

    # 4. Verify the App Reacts
    assert not app.exception

    # Check that the table rendered with Stock/Net columns
    df = app.dataframe[0].value
    assert "Resistors" in df["Category"].values
    assert "In Stock" in df.columns
    assert "Net Need" in df.columns

    # Verify the math: 5 needed - 2 in stock = 3 net need
    # Note: Checking value existence in column is safer than row indexing in tests
    assert 3 in df["Net Need"].values

    assert "Origin" in df.columns
    assert "Circuit Board" in df["Origin"].values

    # Check that download buttons appeared (Integration check)
    # Expected: Shopping List, Stock Update, Generated PDFs, Master Zip
    btns = app.get("download_button")
    assert len(btns) == 4

    # Button index 2 is "Generated PDFs"
    assert "Generated PDFs" in btns[2].label
    # Button index 3 is "Download Complete Build Pack"
    assert "Download Complete Build Pack" in btns[3].label


def test_source_ref_duplication_on_merge(app):
    """
    Verifies that increasing the pedal quantity properly duplicates component references.

    If a slot has Qty=2, the component refs in the source list should appear twice
    (e.g., ['R1', 'R1']) to ensure correct total counts during merging.
    """
    # 1. Inject State directly
    app.session_state["pedal_slots"] = [
        # Slot 1: 2x Pedal
        ProjectSlot(
            id="A",
            name="DupeTest",
            count=2,
            method="Paste Text",
            data="R1 10k",
        ),
    ]

    # 2. Click Generate
    app.button[1].click().run()

    assert not app.exception

    # 3. Inspect the BACKEND inventory directly
    # We inspect the session state because the dataframe aggregates counts,
    # hiding the underlying source list structure we want to verify.
    inventory = app.session_state["inventory"]

    # We expect the source list to be ['R1', 'R1'] because count was 2
    refs = inventory["Resistors | 10k"]["sources"]["DupeTest"]

    assert len(refs) == 2
    assert refs == ["R1", "R1"]


def test_preset_selection_flow(app):
    """
    Verifies the Preset selection workflow.

    Tests:
        1. Switching input method to 'Preset'.
        2. Selecting a specific project from the dropdown.
        3. Verifying the BOM text area auto-populates.
        4. Generating the list successfully.
    """
    # 1. Switch to Preset Mode
    # The radio button is the 2nd widget type in the column group.
    # app.radio[0] corresponds to the first slot's method selector.
    app.radio[0].set_value("Preset").run()

    # 2. Select a specific preset
    # The UI uses 3 Selectboxes: [0]=Source, [1]=Category, [2]=Project
    # We dynamically find a key to ensure test robustness against data changes.
    target_preset = next(k for k in BOM_PRESETS if "Kliche" in k)

    app.selectbox[2].set_value(target_preset).run()

    # 3. Verify Text Area populated
    # The Kliche preset is known to contain the charge pump "TC1044SCPA"
    assert "TC1044SCPA" in app.text_area[0].value

    # 4. Generate Master List (Button index 1)
    app.button[1].click().run()

    # 5. Verify Output
    assert not app.exception
    df = app.dataframe[0].value
    assert "TC1044SCPA" in df["Part"].values


def test_input_method_state_clearing(app):
    """
    Verifies that switching input methods flushes the data buffer.

    This ensures that data from a 'Preset' doesn't persist if the user
    switches back to 'Paste Text' mode.
    """
    # 1. Start in Preset Mode
    app.radio[0].set_value("Preset").run()

    # Ensure data is present (Auto-load first preset logic)
    assert app.text_area[0].value != ""

    # 2. Switch to Paste Text
    app.radio[0].set_value("Paste Text").run()

    # 3. Verify Empty (The 'Flush' logic worked)
    assert app.text_area[0].value == ""

    # 4. Switch back to Preset
    app.radio[0].set_value("Preset").run()

    # 5. Verify data re-loaded (Auto-load logic worked)
    assert app.text_area[0].value != ""
