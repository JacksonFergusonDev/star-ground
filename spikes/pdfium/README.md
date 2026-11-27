# pdfium extraction spike

A reference experiment, not production code: can pypdfium2 replace pdfminer as the thing that reads a PDF's characters, ruled lines and images, while pdfplumber and every Star Ground reader stay as they are? Nothing outside this folder uses it.

## How it works

`GuardedPage` (`src/star_ground/bom_lib/pdf/guard.py`) is where pdfplumber gets a page's objects. `pdfium_page.install()` replaces its `parse_objects` with one that builds the same dictionaries (`char`, `line`, `rect`, `curve`, `image`) from pdfium, and turns off `parse_page`'s pdfminer layout.

- **Characters** come from pdfium's text page: loose character box, origin, matrix and font name. A character's `size` is pdfium's font size times the text matrix's scale, since pdfium reports the raw `Tf` size. Letters of a ligature share one box, so they merge into one character, as pdfminer reads them. Generated spaces and newlines are skipped.
- **Paths** are classified as a line (two points), a rectangle (an axis-aligned four- or five-point path) or a curve, from the page object's own matrix.
- **Images** use pdfium's bounds.

## Results

Measured 2026-11-27 on an M-series Mac, against the 19 sample PDFs, on top of the cache and title changes in [PR 330](https://github.com/JacksonFergusonDev/star-ground/pull/330).

| Check | Result |
|---|---|
| Whole parse compared with pdfminer's (parts, quantities, refs, warnings, title, variants) | 19 of 19 identical |
| `tests/test_pdf_snapshots.py`, `tests/test_parser_accuracy.py` | 40 of 40 pass |
| Fresh-process wall time, text parse of 18 PDFs | 1.89 s with pdfminer, 0.98 s with pdfium |
| Share of the pdfium run | `_without_overprint` 37%, `read_pages` 22%, `_tables` 20%, pdfium itself about 22% |

Run against the other tests, it passes `test_layout`, `test_ocr`, `test_server`, `test_api` and `test_concurrency`. It fails or hangs the hostile-input guards: 4 in `test_hostile_input`, 1 in `test_error_handling` and 12 of 23 in `test_pdf_memory`, 5 of them hanging (forms inside forms, and pages of millions of rectangles, lines or curves). The spike has no equivalent of `guard.py`'s limits on content bytes, characters and memory. A whole-suite run with the spike never finished.

## Known gaps

- Only top-level paths are read, so a ruled table drawn inside a form is missed. Characters inside forms are read.
- Boxes of rotated text are approximated. Font names lose pdfminer's subset prefix (`JULXGQ+`). Colours, dash and `path` are stubbed.
- pdfium is C++: a bad file can crash the worker where pdfminer raises a Python exception. Cloud Run serves several requests per instance.
- Only 19 documents. The comments in `guard.py` mention a local corpus of 1,241 build documents, which is where this would need to be checked.

## Conclusion at the time

The spike saves about 50 ms per text PDF, while an OCR document takes seconds and a cold start about 6.5 s, so it isn't worth doing for speed alone. If the work continues: rebuild the guards first (count objects and characters before iterating, and test crash isolation), then read paths inside forms, then run the 1,241-document corpus.

`_without_overprint` is the largest remaining cost under either extractor, and can be tuned without pdfium.

## Running it

From the repository root, with the samples fetched (`just fetch-samples`):

```bash
uv run python spikes/pdfium/compare.py
```

That parses every sample both ways and reports each document whose result differs. The other scripts:

- `cold.py` and `cold_spike.py` time a fresh process for pdfminer and for pdfium.
- `stages.py` times the stages of the pdfium run.
- `pytest_plugin.py` installs the spike inside pytest: `PYTHONPATH=spikes/pdfium uv run pytest -p pytest_plugin tests/test_pdf_snapshots.py -n auto`.
- `run_each.py` runs the tests in an ids file one by one with a timeout, to find which hang.
