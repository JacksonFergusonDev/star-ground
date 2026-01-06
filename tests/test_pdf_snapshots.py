import pytest
import os
import json
import logging
from src.bom_lib import parse_pedalpcb_pdf

# Logging Config
# Silence noisy libraries so we can see our own debug logs
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

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
    for key in sorted(inventory.keys()):
        data = inventory[key]
        stable[key] = {
            "qty": data["qty"],
            "refs": sorted(data["refs"]),
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


# 🔍 Gather all PDFs in the samples folder (Recursive)
pdf_files = []
if os.path.exists(SAMPLES_DIR):
    for root, dirs, files in os.walk(SAMPLES_DIR):
        for file in files:
            if file.endswith(".pdf"):
                # We store the full relative path so pytest can find it later
                # e.g. "dirty/Muffin_Fuzz.pdf"
                rel_path = os.path.relpath(os.path.join(root, file), SAMPLES_DIR)
                pdf_files.append(rel_path)


@pytest.mark.parametrize("pdf_rel_path", pdf_files)
def test_pdf_parsing_regression(pdf_rel_path):
    """
    Runs the real parser against a real PDF and compares result to stored snapshot.
    """
    # Reconstruct full path
    pdf_path = os.path.join(SAMPLES_DIR, pdf_rel_path)

    # Flatten the snapshot filename: "dirty/Muffin_Fuzz.pdf" -> "dirty__Muffin_Fuzz.pdf.json"
    # This keeps the snapshots folder flat but organized by name
    snapshot_filename = pdf_rel_path.replace(os.sep, "__") + ".json"

    # 1. Run the REAL Code
    inventory, stats = parse_pedalpcb_pdf(pdf_path, source_name="SnapshotTest")

    # 2. Stabilize Data
    current_result = {
        "metadata": {
            "parts_found": stats["parts_found"],
            "extracted_title": stats["extracted_title"],
        },
        "inventory": stabilize_inventory(inventory),
    }

    # 3. Load & Compare
    expected_result = load_snapshot(snapshot_filename)

    if expected_result is None:
        save_snapshot(snapshot_filename, current_result)
        pytest.fail(
            f"📸 New snapshot created for {pdf_rel_path}. Please inspect {snapshot_filename} manually."
        )

    assert current_result == expected_result, (
        f"⚠️ Output mismatch for {pdf_rel_path}. Parser behavior changed!"
    )
