import pytest
from typing import cast
from streamlit.testing.v1 import AppTest
from collections import defaultdict
from src.bom_lib import InventoryType
from src.presets import BOM_PRESETS


# --- Helpers ---
class MockFile:
    """Fake file object to fool the uploader widget."""

    def __init__(self, name, content):
        self.name = name
        self.content = content.encode("utf-8")

    def getvalue(self):
        return self.content


# --- Fixtures ---
@pytest.fixture
def app():
    at = AppTest.from_file("app.py")
    at.run()
    return at


# --- Tests ---
def test_smoke_check(app):
    assert not app.exception
    assert app.title[0].value == "🎸 Guitar Pedal BOM Manager"


def test_happy_path_text_paste(app):
    bom_input = app.text_area[0]
    raw_bom = "R1 10k\nC1 100n\nU1 TL072"
    bom_input.set_value(raw_bom).run()

    # Click "Generate Master List" (Index 1 in the button list)
    app.button[1].click().run()

    assert not app.exception
    assert app.metric[1].value == "3"
    df = app.dataframe[0].value
    assert "TL072" in df["Part"].values


def test_csv_processing_via_state_injection(app):
    """
    Bypass the broken FileUploader widget by injecting data directly
    into session_state. This verifies the 'Integration' (Data -> UI)
    logic works, even if the test runner is blind to the uploader.
    """
    # 1. Mock the inventory that the CSV parser WOULD have produced
    mock_inventory = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    mock_inventory["Resistors | 10k"]["qty"] = 5
    mock_inventory["Resistors | 10k"]["sources"]["Mock Project"] = ["R1-R5"]

    mock_inventory["Capacitors | 22n"]["qty"] = 2
    mock_inventory["Capacitors | 22n"]["sources"]["Mock Project"] = ["C1", "C2"]

    # Mock Stock
    mock_stock = cast(
        InventoryType,
        defaultdict(lambda: {"qty": 0, "refs": [], "sources": defaultdict(list)}),
    )
    mock_stock["Resistors | 10k"]["qty"] = 2  # Have 2

    mock_stats = {"lines_read": 7, "parts_found": 7, "residuals": []}

    # 2. Inject into session state
    app.session_state["inventory"] = mock_inventory
    app.session_state["stock"] = mock_stock  # <--- Inject Stock
    app.session_state["stats"] = mock_stats

    if "pedal_slots" not in app.session_state:
        app.session_state["pedal_slots"] = [
            {"id": "test", "name": "test", "method": "Paste Text"}
        ]

    # 3. Rerun the app to trigger the "Main Process" block
    app.run()

    # 4. Verify the App Reacts
    assert not app.exception

    # Check that the table rendered with NEW columns
    df = app.dataframe[0].value
    assert "Resistors" in df["Category"].values
    assert "In Stock" in df.columns
    assert "Net Need" in df.columns

    # Verify the math in the displayed table
    # We had 5 in BOM, Mocked 2 in Stock -> Net Need should be 3
    # Note: Streamlit dataframes can be tricky to index by row in tests,
    # but we can check if the value exists in the column.
    assert 3 in df["Net Need"].values

    assert "Origin" in df.columns
    assert "Circuit Board" in df["Origin"].values

    # Check that download buttons appeared (Integration check)
    # Phase 2a added a second download button (Updated Inventory)
    assert len(app.get("download_button")) == 2


def test_source_ref_duplication_on_merge(app):
    """
    Verify that if a slot has Qty=2, the component refs are duplicated
    in the source list (Commit 2).
    """
    # 1. Inject State directly
    app.session_state["pedal_slots"] = [
        # Slot 1: 2x Pedal
        {
            "id": "A",
            "name": "DupeTest",
            "count": 2,
            "method": "Paste Text",
            "last_method": "Paste Text",
            "data": "R1 10k",
        },
    ]

    # 2. Click Generate
    app.button[1].click().run()

    assert not app.exception

    # 3. Inspect the BACKEND inventory directly
    # (The dataframe shows the math, but we want to check the hidden source list)
    inventory = app.session_state["inventory"]

    # We expect the source list to be ['R1', 'R1'] because count was 2
    refs = inventory["Resistors | 10k"]["sources"]["DupeTest"]

    assert len(refs) == 2
    assert refs == ["R1", "R1"]


def test_preset_selection_flow(app):
    """
    Test the full Preset workflow: Select Method -> Select Preset -> Generate.
    """
    # 1. Switch to Preset Mode
    # The radio button is the 2nd widget in the columns (Name, Qty, Method...)
    # AppTest organizes by type. app.radio[0] is the first slot's method selector.
    app.radio[0].set_value("Preset").run()

    # 2. Select a specific preset
    # New UI has 3 Selectboxes: [0]=Source, [1]=Category, [2]=Project
    # Dynamically find the key to handle stale presets.py (Pedalpcb vs PedalPCB)
    target_preset = next(k for k in BOM_PRESETS.keys() if "Kliche" in k)

    app.selectbox[2].set_value(target_preset).run()

    # 3. Verify Text Area populated
    # The Kliche preset contains the charge pump "TC1044SCPA"
    assert "TC1044SCPA" in app.text_area[0].value

    # 4. Generate Master List (Button index 1: [Add, Generate])
    app.button[1].click().run()

    # 5. Verify Output
    assert not app.exception
    df = app.dataframe[0].value
    assert "TC1044SCPA" in df["Part"].values


def test_input_method_state_clearing(app):
    """
    Verify that switching methods clears the data (Regression test for the 'Flush' fix).
    """
    # 1. Start in Preset Mode
    app.radio[0].set_value("Preset").run()

    # Ensure data is there (Auto-load first preset logic)
    assert app.text_area[0].value != ""

    # 2. Switch to Paste Text
    app.radio[0].set_value("Paste Text").run()

    # 3. Verify Empty (The 'Flush' worked)
    assert app.text_area[0].value == ""

    # 4. Switch back to Preset
    app.radio[0].set_value("Preset").run()

    # 5. Verify data re-loaded (Auto-load logic worked)
    assert app.text_area[0].value != ""
