import pytest
from streamlit.testing.v1 import AppTest
from collections import defaultdict


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
    app.button(key="text_submit").click().run()

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
    mock_inventory = defaultdict(int)
    mock_inventory["Resistors | 10k"] = 5
    mock_inventory["Capacitors | 22n"] = 2

    mock_stats = {"lines_read": 7, "parts_found": 7, "residuals": []}

    # 2. Inject into session state
    app.session_state["inventory"] = mock_inventory
    app.session_state["stats"] = mock_stats

    # 3. Rerun the app to trigger the "Main Process" block
    app.run()

    # 4. Verify the App Reacts
    # The dataframe should now be visible
    assert not app.exception

    # Check that the table rendered
    df = app.dataframe[0].value
    assert "Resistors" in df["Category"].values
    assert "10k" in df["Part"].values
    assert "Section" in df.columns
    assert "Parsed BOM" in df["Section"].values

    # Check that download buttons appeared (Integration check)
    assert len(app.get("download_button")) == 1
