# 🛡️ Verification Strategy

> **"Reliability is not an accident. It is a proven state."**

This test suite is designed to enforce the **Deterministic** nature of the Star Ground engine. We do not rely on simple "happy path" checks; we utilize **Property-Based Testing** and **Snapshot Regression** to mathematically prove the system's stability against entropy (random input data).

## Testing Layers

### 1. Snapshot Regression (The "Golden Master")

**File:** `tests/test_pdf_snapshots.py`

PDF parsing is inherently fragile. A minor update to a regex pattern could silently break support for a legacy PDF format. To prevent this, we use **Snapshot Testing**.

- **Strategy:** We maintain a library of "Golden Master" PDFs (`tests/samples/`) and their expected JSON outputs (`tests/snapshots/`). The PDFs belong to their publishers, so the repository keeps only `tests/samples/manifest.json` (each document's URL and SHA-256); `just fetch-samples` downloads and verifies them, and `just test` does so first.
- **Mechanism:** Every CI run parses the raw PDFs and diffs the output against the stored JSON.
- **The Guarantee:** Zero regression. If a single resistor value changes in the output, the build fails immediately.

### 2. Ground-Truth Accuracy (The "Answer Key")

**Files:** `tests/test_parser_accuracy.py`, `tests/accuracy.py`, `tests/golden/`

Snapshots prove the output did not *change*; they cannot prove it is *correct*,
because they are generated from the parser itself. The golden files are
hand-verified transcriptions of each sample PDF's parts list, so they can.

- **Strategy:** Every designator is graded against the golden file (correct,
  missing, extra, wrong category, wrong value), and every failure is checked
  for a matching parser warning.
- **Mechanism:** `tests/golden/baseline.json` records the accepted failures. CI
  fails on any new failure, any failure that loses its warning, any false
  alarm, and any improvement that has not been locked in.
- **The Guarantee:** Accuracy and warning coverage only ratchet upward. See
  `tests/golden/README.md` for the workflow and labeling rules.

### 3. Property-Based Fuzzing (`Hypothesis`)

**File:** `tests/test_parser.py`

Standard unit tests often suffer from "tester bias"—we only test the inputs we expect.

- **Strategy:** We use the [Hypothesis](https://hypothesis.readthedocs.io/) library to "fuzz" our logic.
- **Mechanism:** Instead of manually typing test cases, we define **invariants**: the parser never crashes on arbitrary text, and the buying math scales soundly with quantity. Hypothesis generates the inputs to try to break them.
- **The Guarantee:** Garbage input yields an empty or partial list, never a crash.

### 4. The Backend API

**File:** `tests/test_api.py`

- **Mechanism:** Drives `src/api.py` as a frontend would, with no UI: merging projects, refusing an unchosen build and reusing a reading untouched, carrying input errors to the master list, subtracting stock, and the generated documents named by each project's label.
- **The Boundary:** Scans the imports of every backend module and fails if one imports Streamlit or the app's session state.

### 5. Headless Integration

**File:** `tests/test_app.py`

To ensure the UI is decoupled from the browser state, we run headless simulations using `Streamlit.AppTest`.

- **Mechanism:** The test runner instantiates the full application kernel in memory, simulates user interactions (clicks, uploads, form submissions), and asserts the state of the dataframes.
- **Coverage:** Verifies the full "Paste → Parse → Download" lifecycle, choosing a build and accepting OCR's parts, without a browser driver (Selenium/Puppeteer).

### 6. URL Fetching over Real Sockets

**Files:** `tests/test_fetch.py`, `tests/test_fetch_server.py`, `tests/test_fetch_live.py`

"From URL" fetches on the hosted server's behalf, so its safety rules are tested against real connections, not mocks.

- **Rules** (`test_fetch.py`): which schemes, ports and addresses are allowed (IPv4-mapped and NAT64 forms of private addresses included), share-link rewriting, and the pinned URL and TLS hostname.
- **Local servers** (`test_fetch_server.py`): an HTTP and an HTTPS server on 127.0.0.1, with a test certificate authority from `trustme`. Only name resolution is replaced (`*.test` names); requests, urllib3, TLS, redirects and streaming run for real. Covers DNS rebinding, certificate name checks, https-to-http redirects, redirect loops, declared, streamed and gzip-bomb size limits, a trickling server against the deadline, a stalled server, error statuses, and the whole "From URL" parse of a PDF, a CSV and a web page. Nothing leaves the machine.
- **Live** (`test_fetch_live.py`, marker `network`): one real document per publisher, a GitHub file page, Tonepad's download page, and a public name that resolves to 127.0.0.1. Deselected by default; run them before a release or when a publisher's site changes:

```bash
uv run pytest -m network
```

### 7. Package Integrity

**File:** `tests/test_package.py`

- **Mechanism:** Verifies that the `src.bom_lib` package exposes the correct public API and is free of circular import dependencies.

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

### Accuracy Scorecard

Print per-document accuracy, with every failure and whether it was warned:

```bash
just accuracy
```

Score another corpus (for example a local, uncommitted one) with the same machinery. With `--ocr`, only the documents OCR reads are scored, with OCR's parts accepted as a builder would:

```bash
uv run python -m tests.accuracy --golden path/to/golden --samples path/to/pdfs --ocr -v
```

### Targeted Testing

Run only the Fuzzing engine:

```bash
uv run pytest tests/test_parser.py
```

Run only the Snapshot regression:

```bash
uv run pytest tests/test_pdf_snapshots.py
```
