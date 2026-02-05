"""
Regression Testing Suite for PDF Parsing.

This module uses "Snapshot Testing" to ensure that the PDF parsing logic remains
stable and deterministic. It runs the parser against a set of real-world PDF samples
(stored in `tests/samples`) and compares the output against verified JSON "Truth" files
(stored in `tests/snapshots`).

If a code change intentionally alters the parsing output, the snapshots must be
regenerated.
"""

import json
import logging
import os

import pytest

from src.bom_lib import parse_pedalpcb_pdf

# --- Logging Config ---
# Silence noisy third-party libraries so we can focus on our own debug logs
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

# --- Configuration ---
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")

# Ensure snapshot directory exists
if not os.path.exists(SNAPSHOTS_DIR):
    os.makedirs(SNAPSHOTS_DIR)


def stabilize_inventory(inventory):
    """
    Normalizes the inventory dictionary for deterministic JSON comparison.

    Converts `defaultdict`s to regular dicts and sorts all lists (refs, sources).
    This ensures that two identical inventories produce identical JSON strings,
    ignoring the random order of dictionary keys or set iterations.

    Args:
        inventory (InventoryType): The raw inventory dictionary from the parser.

    Returns:
        dict: A sorted, standard dictionary representation of the inventory.
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
    """
    Loads the 'Truth' JSON snapshot if it exists.

    Args:
        filename (str): The filename of the snapshot to load.

    Returns:
        dict or None: The parsed JSON data, or None if the file is missing.
    """
    path = os.path.join(SNAPSHOTS_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(filename, data):
    """
    Saves the current output as the new 'Truth' snapshot.

    Args:
        filename (str): The filename to save.
        data (dict): The data to serialize to JSON.
    """
    path = os.path.join(SNAPSHOTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


# --- Test Discovery ---
# Recursively gather all PDF files in the samples folder
pdf_files = []
if os.path.exists(SAMPLES_DIR):
    for root, _dirs, files in os.walk(SAMPLES_DIR):
        for file in files:
            if file.endswith(".pdf"):
                # We store the full relative path so pytest can find it later
                # e.g. "dirty/Muffin_Fuzz.pdf"
                rel_path = os.path.relpath(os.path.join(root, file), SAMPLES_DIR)
                pdf_files.append(rel_path)


@pytest.mark.parametrize("pdf_rel_path", pdf_files)
def test_pdf_parsing_regression(pdf_rel_path):
    """
    Regression Test: Compares parser output against stored snapshots.

    Runs the production parser against a real PDF sample and asserts that the
    output matches the stored JSON snapshot exactly.

    If the snapshot is missing, this test generates it and fails intentionally,
    prompting the developer to verify the new snapshot manually.

    Args:
        pdf_rel_path (str): Relative path to the PDF sample file.
    """
    # Reconstruct full path
    pdf_path = os.path.join(SAMPLES_DIR, pdf_rel_path)

    # Flatten the snapshot filename to avoid deep directory structures in snapshots/
    # Example: "dirty/Muffin_Fuzz.pdf" -> "dirty__Muffin_Fuzz.pdf.json"
    snapshot_filename = pdf_rel_path.replace(os.sep, "__") + ".json"

    # 1. Run the Real Code
    inventory, stats = parse_pedalpcb_pdf(pdf_path, source_name="SnapshotTest")

    # 2. Stabilize Data for Comparison
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
