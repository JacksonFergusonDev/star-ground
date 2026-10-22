import os
import uuid

import pytest
from streamlit.testing.v1 import AppTest

from src.bom_lib import (
    BOM_PRESETS,
    ComponentCategory,
    ComponentKey,
    InputMethod,
    ProjectSlot,
)
from src.bom_lib.fetch import FetchedDocument
from src.bom_lib.types import Inventory


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
    at = AppTest.from_file("../app.py", default_timeout=30)
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
    k_res = ComponentKey(ComponentCategory.RESISTORS, "10k")
    k_cap = ComponentKey(ComponentCategory.CAPACITORS, "22n")

    mock_inventory = Inventory()
    mock_inventory[k_res]["qty"] = 5
    mock_inventory[k_res]["sources"]["Mock Project"] = ["R1-R5"]

    mock_inventory[k_cap]["qty"] = 2
    mock_inventory[k_cap]["sources"]["Mock Project"] = ["C1", "C2"]

    # Mock Stock (User already has 2x 10k resistors)
    mock_stock = Inventory()
    mock_stock[k_res]["qty"] = 2

    mock_stats = {"lines_read": 7, "parts_found": 7, "residuals": []}

    # 2. Inject into session state
    app.session_state["inventory"] = mock_inventory
    app.session_state["stock"] = mock_stock  # Inject Stock to trigger "Net Need" logic
    app.session_state["stats"] = mock_stats

    # Ensure at least one slot exists to prevent UI errors
    if "pedal_slots" not in app.session_state:
        app.session_state["pedal_slots"] = [
            ProjectSlot(id=uuid.uuid4(), name="test", method=InputMethod.PASTE_TEXT)
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
    # Expected: Shopping List, Supplier Cart, Stock Update, Generated PDFs,
    # Master Zip
    btns = app.get("download_button")
    assert len(btns) == 5
    assert "Supplier Cart" in btns[1].label
    assert "Generated PDFs" in btns[3].label
    assert "Download Complete Build Pack" in btns[4].label


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
            id=uuid.uuid4(),
            name="DupeTest",
            count=2,
            method=InputMethod.PASTE_TEXT,
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
    k_res = ComponentKey(ComponentCategory.RESISTORS, "10k")
    refs = inventory[k_res]["sources"]["DupeTest"]

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
    app.radio[0].set_value(InputMethod.PRESET).run()

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
    app.radio[0].set_value(InputMethod.PRESET).run()

    # Ensure data is present (Auto-load first preset logic)
    assert app.text_area[0].value != ""

    # 2. Switch to Paste Text
    app.radio[0].set_value(InputMethod.PASTE_TEXT).run()

    # 3. Verify Empty (The 'Flush' logic worked)
    assert app.text_area[0].value == ""

    # 4. Switch back to Preset
    app.radio[0].set_value(InputMethod.PRESET).run()

    # 5. Verify data re-loaded (Auto-load logic worked)
    assert app.text_area[0].value != ""


def test_multi_circuit_pdf_requires_a_build_choice(app, monkeypatch):
    """A multi-circuit PDF asks which build, blocks Generate until chosen, and
    then builds exactly the chosen variant, labeled with its name."""
    samples = os.path.join(os.path.dirname(__file__), "samples", "dirty")

    def fake_fetch(url):
        with open(os.path.join(samples, url.rsplit("/", 1)[-1]), "rb") as f:
            return FetchedDocument(f.read(), "application/pdf", url)

    monkeypatch.setattr("src.bom_lib.strategies.context.fetch_url", fake_fetch)
    slot = ProjectSlot(method=InputMethod.FROM_URL)
    app.session_state.pedal_slots = [slot]
    app.session_state[f"method_{slot.id}"] = InputMethod.FROM_URL
    app.session_state[f"url_{slot.id}"] = "https://example.test/Ungula.pdf"
    app.run()

    (picker,) = [sb for sb in app.selectbox if sb.label == "Build"]
    assert picker.options == ["Stock", "Cleft Mod"]
    assert picker.value is None  # no default build

    generate = next(b for b in app.button if b.label == "Generate Master List")
    generate.click().run()
    assert any("Choose which build" in e.value for e in app.error)
    assert not app.session_state.inventory

    picker.select("Cleft Mod").run()
    next(b for b in app.button if b.label == "Generate Master List").click().run()
    assert not app.error
    inventory = app.session_state.inventory
    cleft_q1 = ComponentKey(ComponentCategory.TRANSISTORS, "2N5089")
    assert "Q1" in inventory[cleft_q1]["sources"]["Ungula (Cleft Mod)"]
    # The Cleft mod omits C6.
    assert not any(
        "C6" in refs for d in inventory.values() for refs in d["sources"].values()
    )


def test_ocr_builds_are_chosen_after_accepting_them(app, monkeypatch):
    """An image parts list OCR read as several builds: the build picker
    appears once the OCR parts are accepted, and Generate builds the choice."""
    from src.bom_lib import create_empty_inventory, create_empty_stats
    from src.bom_lib.enums import OcrVerdict
    from src.bom_lib.types import (
        BuildVariant,
        OcrAttempt,
        ParseResult,
        make_component_key,
    )

    def build(value):
        inventory = create_empty_inventory()
        key = make_component_key(ComponentCategory.RESISTORS, value)
        for ref in ("R1", "R2"):
            inventory.add_part("__pending__", key, ref, 1)
        return inventory

    attempt = OcrAttempt(
        verdict=OcrVerdict.COMPLETE,
        inventory=Inventory(),
        warnings=(),
        pages=(0,),
        parts_read=2,
        suspect=0,
        supplements=False,
        variants=(
            BuildVariant("Octavia", build("10k"), 2),
            BuildVariant("Vintage", build("1k"), 2),
        ),
    )

    def fake_process(self, method, data, source_name):
        return ParseResult(create_empty_inventory(), create_empty_stats(), ocr=attempt)

    monkeypatch.setattr(
        "src.bom_lib.strategies.context.BOMParserContext.process", fake_process
    )
    slot = ProjectSlot(method=InputMethod.FROM_URL)
    app.session_state.pedal_slots = [slot]
    app.session_state[f"method_{slot.id}"] = InputMethod.FROM_URL
    app.session_state[f"url_{slot.id}"] = "https://example.test/Octavia.pdf"
    app.run()

    assert not [sb for sb in app.selectbox if sb.label == "Build"]
    (accept,) = [c for c in app.checkbox if "read by OCR" in c.label]
    accept.check().run()
    (picker,) = [sb for sb in app.selectbox if sb.label == "Build"]
    assert picker.options == ["Octavia", "Vintage"]

    picker.select("Vintage").run()
    next(b for b in app.button if b.label == "Generate Master List").click().run()
    assert not app.error
    inventory = app.session_state.inventory
    one_k = ComponentKey(ComponentCategory.RESISTORS, "1k")
    assert any("Vintage" in source for source in inventory[one_k]["sources"])


def test_an_untitled_document_is_named_after_its_file(app, monkeypatch):
    """A document that prints no title (Five Cats' image inserts) names its
    project after the file, not 'Project #1'."""
    monkeypatch.setattr(
        "src.bom_lib.strategies.context.fetch_url",
        lambda url: FetchedDocument(b"R1 10k\nC1 100n\n", "text/plain", url),
    )
    slot = ProjectSlot(method=InputMethod.FROM_URL)
    app.session_state.pedal_slots = [slot]
    app.session_state[f"method_{slot.id}"] = InputMethod.FROM_URL
    app.session_state[f"url_{slot.id}"] = (
        "https://example.test/files/FCP-Tone%20Monarch.txt"
    )
    app.run()

    next(b for b in app.button if b.label == "Generate Master List").click().run()
    assert app.session_state.pedal_slots[0].name == "FCP-Tone Monarch"


def test_a_part_with_several_warnings_is_one_part_to_review(app, monkeypatch):
    """Aion Amethyst's C1 has a footnote and a build note: one part to review,
    listed once with both reasons."""
    from src.bom_lib import create_empty_inventory, create_empty_stats
    from src.bom_lib.enums import ParseWarningKind
    from src.bom_lib.types import ParseResult, ParseWarning, make_component_key

    def fake_process(self, method, data, source_name):
        inventory = create_empty_inventory()
        inventory.add_part(
            source_name, make_component_key(ComponentCategory.CAPACITORS, "1n"), "C1", 1
        )
        stats = create_empty_stats()
        stats["parts_found"] = 1
        stats["warnings"] = [
            ParseWarning(
                ParseWarningKind.FOOTNOTE, "C1", "'470pF MLCC for Aqua Puss.'"
            ),
            ParseWarning(ParseWarningKind.FOOTNOTE, "C1", "the build notes change it"),
        ]
        return ParseResult(inventory, stats, title="Amethyst")

    monkeypatch.setattr(
        "src.bom_lib.strategies.context.BOMParserContext.process", fake_process
    )
    slot = ProjectSlot(method=InputMethod.PASTE_TEXT)
    app.session_state.pedal_slots = [slot]
    app.session_state[f"text_{slot.id}"] = "C1 1n"
    app.run()
    next(b for b in app.button if b.label == "Generate Master List").click().run()

    assert any("1 part(s) need review" in w.value for w in app.warning)
    assert [e.label for e in app.expander if "to review" in e.label] == [
        "Amethyst: 1 to review"
    ]


def test_results_stay_while_the_inputs_are_unchanged(app):
    app.text_area[0].set_value("R1 10k\nC1 100n").run()
    app.button[1].click().run()
    assert len(app.dataframe) == 1

    app.run()
    assert len(app.dataframe) == 1
    assert not app.caption or "inputs changed" not in app.caption[-1].value


@pytest.mark.parametrize(
    "change",
    [
        lambda at: at.text_area[0].set_value("R1 22k\nC1 100n"),
        lambda at: at.number_input[0].set_value(2),
        lambda at: at.text_input[0].set_value("Renamed"),
        lambda at: at.radio[0].set_value(InputMethod.FROM_URL),
    ],
    ids=["text", "qty", "name", "method"],
)
def test_changing_an_input_clears_the_old_results(app, change):
    app.text_area[0].set_value("R1 10k\nC1 100n").run()
    app.button[1].click().run()
    assert len(app.dataframe) == 1

    change(app).run()

    assert not app.exception
    assert len(app.dataframe) == 0
    assert app.session_state["inventory"] is None
    assert any("inputs changed" in c.value for c in app.caption)

    if app.text_area:
        app.text_area[0].set_value("R1 10k").run()
    else:
        app.text_input[1].set_value("").run()
    app.button[1].click().run()
    assert not any("inputs changed" in c.value for c in app.caption)
