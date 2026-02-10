# 🛡️ Verification Strategy

> **"Reliability is not an accident. It is a proven state."**

This test suite is designed to enforce the **Deterministic** nature of the Star Ground engine. We do not rely on simple "happy path" checks; we utilize **Property-Based Testing** and **Snapshot Regression** to mathematically prove the system's stability against entropy (random input data).

## Testing Layers

### 1. Snapshot Regression (The "Golden Master")
**File:** `tests/test_pdf_snapshots.py`

PDF parsing is inherently fragile. A minor update to a regex pattern could silently break support for a legacy PDF format. To prevent this, we use **Snapshot Testing**.
* **Strategy:** We maintain a library of "Golden Master" PDFs (`tests/samples/`) and their expected JSON outputs (`tests/snapshots/`).
* **Mechanism:** Every CI run parses the raw PDFs and diffs the output against the stored JSON.
* **The Guarantee:** Zero regression. If a single resistor value changes in the output, the build fails immediately.

### 2. Property-Based Fuzzing (`Hypothesis`)
**File:** `tests/test_parser.py`

Standard unit tests often suffer from "tester bias"—we only test the inputs we expect.
* **Strategy:** We use the [Hypothesis](https://hypothesis.readthedocs.io/) library to "fuzz" our logic.
* **Mechanism:** Instead of manually typing test cases, we define **invariants** (e.g., `net_needs` must never be negative). Hypothesis generates thousands of random, chaotic inputs (Unicode injection, edge-case floats, massive integers) to try and break these rules.
* **The Guarantee:** The math holds true even when the input data is garbage.

### 3. Headless Integration
**File:** `tests/test_app.py`

To ensure the UI is decoupled from the browser state, we run headless simulations using `Streamlit.AppTest`.
* **Mechanism:** The test runner instantiates the full application kernel in memory, simulates user interactions (clicks, uploads, form submissions), and asserts the state of the dataframes.
* **Coverage:** Verifies the full "Paste → Parse → Download" lifecycle without requiring a browser driver (Selenium/Puppeteer).

### 4. Package Integrity
**File:** `tests/test_package.py`
* **Mechanism:** Verifies that the `src.bom_lib` package exposes the correct public API and is free of circular import dependencies.

---

## Running Tests

### Standard Execution
Run the full suite using `uv`:
```bash
uv run pytest
```
### Snapshot Management

If you make a change that intentionally alters the parser output (e.g., fixing a bug that changes how data is extracted), the snapshot tests will fail. To accept the new output as the new "Truth":
```bash
uv run pytest --snapshot-update
```
*Note: This command will overwrite the JSON files in `tests/snapshots/`. Always review the `git diff` of the snapshots before committing to ensure the changes are correct.*

### Targeted Testing

Run only the Fuzzing engine:
```bash
uv run pytest tests/test_parser.py
```

Run only the Snapshot regression:
```bash
uv run pytest tests/test_pdf_snapshots.py
```
