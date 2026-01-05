import pytest
import os
import json
from src.bom_lib import parse_pedalpcb_pdf

# 📂 Config
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")

# Ensure snapshot dir exists
if not os.path.exists(SNAPSHOTS_DIR):
    os.makedirs(SNAPSHOTS_DIR)


def stabilize_inventory(inventory):
    """
    Converts defaultdicts to regular dicts and sorts lists
    to ensure JSON output is deterministic (stable) for comparison.
    """
    stable = {}
    # Sort keys to ensure order doesn't trigger false diffs
    for key in sorted(inventory.keys()):
        data = inventory[key]
        stable[key] = {
            "qty": data["qty"],
            "refs": sorted(data["refs"]),
            # Convert inner defaultdict to dict
            "sources": {k: sorted(v) for k, v in data["sources"].items()},
        }
    return stable


def load_snapshot(filename):
    """Loads the 'Truth' JSON if it exists."""
    path = os.path.join(SNAPSHOTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(filename, data):
    """Saves the current output as the new 'Truth'."""
    path = os.path.join(SNAPSHOTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


# 🔍 Gather all PDFs in the samples folder
# This generates a test case for every .pdf file automatically
pdf_files = (
    [f for f in os.listdir(SAMPLES_DIR) if f.endswith(".pdf")]
    if os.path.exists(SAMPLES_DIR)
    else []
)


@pytest.mark.parametrize("pdf_filename", pdf_files)
def test_pdf_parsing_regression(pdf_filename):
    """
    Runs the real parser against a real PDF and compares result to stored snapshot.
    """
    pdf_path = os.path.join(SAMPLES_DIR, pdf_filename)
    snapshot_name = f"{pdf_filename}.json"

    # 1. Run the REAL Code (No Mocks!)
    # We use a generic source name because we just care about parsing accuracy
    inventory, stats = parse_pedalpcb_pdf(pdf_path, source_name="SnapshotTest")

    # 2. Stabilize Data for Comparison
    current_result = {
        "metadata": {
            "parts_found": stats["parts_found"],
            "extracted_title": stats["extracted_title"],
        },
        "inventory": stabilize_inventory(inventory),
    }

    # 3. Load previous snapshot
    expected_result = load_snapshot(snapshot_name)

    # 4. The "Golden Master" Logic
    if expected_result is None:
        # First time running this PDF? Save it and fail (so you look at it).
        save_snapshot(snapshot_name, current_result)
        pytest.fail(
            f"📸 New snapshot created for {pdf_filename}. Please inspect {snapshot_name} manually to verify correctness."
        )

    # 5. Compare
    # If this fails, it means the parser output CHANGED.
    # You either broke the parser, or you improved it and need to update the snapshot.
    assert current_result == expected_result, (
        f"⚠️ Output mismatch for {pdf_filename}. Parser behavior changed!"
    )
