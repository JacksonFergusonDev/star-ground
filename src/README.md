# 🏗️ Architecture: The Signal Processing Pipeline

The `src/` directory is structured as a unidirectional data pipeline. Data flows from **High Entropy** (Raw PDFs/Text) to **Low Entropy** (Structured Objects, Manufacturing Artifacts).

The system enforces strict separation between **Ingestion**, **Normalization**, **State Management**, and **Presentation**.

Everything in `src/` except `app_state.py` is the **backend**: it imports no UI framework, and a frontend reaches it through `api.py`. `tests/test_api.py` fails if a backend module imports Streamlit or the app's state.

---

## 📂 Module Map

### 1. Ingestion Layer (The Senses)

- **`bom_lib/strategies/`**: Input dispatch.
  - **Role:** `BOMParserContext` picks a strategy by input (PDF, CSV, pasted text). A URL is first fetched by `bom_lib/fetch.py`, since the hosted app fetches on the user's behalf. A `FetchPolicy` sets its reach: `http(s)` on ports 80 and 443 only, every resolved address public (IPv4 inside IPv6 unwrapped), each redirect checked again and never from https to http, the connection pinned to the checked address (so DNS can't rebind the host in between) with TLS still verified against the hostname, 30 MB at most counted after decompression, and 30 seconds for the whole download. Share links (GitHub file pages, Google Drive, Dropbox) are rewritten to the file. The result is routed by content: a PDF, a CSV (read as columns), or text; a web page or a damaged PDF is explained to the builder.
- **`bom_lib/parser.py`**: The entry point for dynamic data.
  - **Strategy:** Implements a **Hybrid Strategy**. It maps ruled PDF tables using `pdfplumber`, then runs a column-aware layout pass to recover everything the tables missed (or everything, for borderless multi-column parts lists).
- **`bom_lib/layout.py`**: The layout reader.
  - **Role:** Groups positioned words into lines and *cells* (split at column-sized gaps), then pairs each designator cell with its value cell. This keeps multi-word values whole (`3mm Red LED`, `10u (25V or higher)`) without bleeding into the neighboring column.
  - **Counted rows:** A cell that counts parts by value (`4 - 10k`, Tonepad) is read with its column's heading (`counted_parts`). The classifier takes its category from a word in the value (`1n4001 diode`) or from the heading (`Resistors`, `Pots`, a designator prefix such as `RX`); a row neither names is not guessed. Counted parts have no designator: `PartData.counted` records how many one build of each project needs, and the build planner gives a row to each build only when every build's list repeats it (otherwise it is common).
- **`bom_lib/ocr.py`**: The OCR fallback for parts lists printed as images.
  - **Role:** When a parse looks incomplete (no parts list, or designators missing from it), renders the candidate pages, reads them with RapidOCR, keeps only each page's parts *table* (evenly spaced rows of aligned listings), and runs the words through the same readers as a text layer. The reading is graded (`Looks complete` / `Partial` / `Unusable`) and offered on `ParseResult.ocr`; its parts join the list only through `ParseResult.accept_ocr()`, each with a `Read by OCR` warning. OCR runs one page at a time under a lock, at about 500 MB and 2.5 s a page.
- **`bom_lib/designators.py`**: The designator grammar.
  - **Role:** Decides what is a reference designator (`R1A`, `IC_A`, `LEDA`, `CLR1`) and what category its prefix implies; anything else is a named control classified by its value. Shared by the classifier and the layout reader so they cannot disagree.
- **`bom_lib/builds.py`**: The build planner.
  - **Role:** Splits extracted entries into common parts, *variants* (separate circuits in one document: Muffin Fuzz's versions, a Germanium and a Silicon list, a "Cleft mod") and *options* (interchangeable parts: through-hole or SMD). A multi-circuit result has no default build: `ParseResult.select()` must be called with the builder's choice, and the app asks for it.
- **`bom_lib/verification.py`**: The self-checks.
  - **Role:** Independently re-reads the document and reports every place the output may disagree with it (unextracted designators, conflicting duplicates, category contradictions, unparseable values, bill-of-materials mismatches). The invariant: the parser may be wrong, but never *silently*.
- **`bom_lib/presets.py`**: The entry point for static data.
  - **Role:** Acts as the "Reference Library." It allows users to load pre-verified BOMs for known circuits (PedalPCB and Tayda projects) without parsing a file.
- **`bom_lib/_presets_data.py`**: The static database.
  - **Note:** This file is **auto-generated** by `tools/generate_presets.py`. It contains the raw text blobs of valid BOMs to ensure the presets are immutable and version-controlled.

### 2. Classification & Normalization (The Filter)

- **`bom_lib/classifier.py`**: The identification engine.
  - **Heuristics:** Uses reference designators (`R1`, `U1`) and value signatures to categorize components. It determines if `C1` is a Ceramic Disc (low profile) or an Electrolytic (high profile) based on value thresholds.
- **`bom_lib/grammar/value_parser.py`**: The value grammar.
  - **Role:** The "Unit Enforcer." A `pyparsing` grammar converts chaotic strings (`4k7`, `4.7k`, `4,700R`, `0R1`) into exact `Decimal` values ($4.7 \times 10^3$), so equal values merge into one line.
- **`bom_lib/units.py`**: The shared `Pint` unit registry, so a value always carries its unit and a voltage is never compared with a resistance.
- **`bom_lib/utils.py`**: Designator helpers: range expansion (`R1-R5`), natural sorting, display strings.
- **`bom_lib/constants.py`**: The knowledge base.
  - **Content:** Stores the "Silicon Sommelier" dictionaries (audio-grade substitutions), Regex patterns, and SI multipliers. This separates *configuration* from *logic*.
- **`bom_lib/types.py`**: The schema definitions.
  - **Role:** Defines the pipeline's types: `Inventory` and `PartData`, `StatsDict`, `ParseWarning`, and `ParseResult` with its builds and OCR attempt. Discrete choices are enums (`bom_lib/enums.py`), never magic strings.

### 3. Logic Kernel (The Brain)

- **`bom_lib/manager.py`**: Pure Python state management.
  - **Role:** The "Controller." It calculates `Net Needs = BOM - Stock` and handles the merging of multiple projects into a single master list.

- **`bom_lib/sourcing.py`**: Yield Management ("Nerd Economics").
  - **Logic:** Applies category-specific buffers to the net need:
    - *Resistors:* +5, rounded up to the next 10 (economy of scale).
    - *Capacitors:* +5; +10 for 100n bypass caps; +1 at 1µF and above.
    - *Diodes:* +5, at least 10. *Transistors, crystals, optoelectronics:* +1.
    - *ICs:* Exact count (high cost), with a socket recommended.
    - *Inductors, transformers, relays:* Exact count.
  - **Parts included with the PCB** are subtracted from what to buy.
  - **Hardware Injection:** Adds a standard kit per pedal (enclosure, jacks, footswitch, status LED with one resistor per LED, knobs, wire), skipping whatever the document lists itself (`provided_hardware`).

### 4. Output Layer (The Hands)

- **`pdf_generator.py`**: Manufacturing Artifacts.
  - **Feature:** **Z-Height Sorting**. It reorders the build instructions so the operator installs components from shortest (Resistors) to tallest (Capacitors), optimizing mechanical stability.

- **`exporters.py`**: Digital serialization.
  - **Feature:** Generates CSVs and Excel-compatible strings, injecting `=HYPERLINK()` formulas for one-click purchasing.
  - **Supplier cart:** A `Quantity, Manufacturer Part Number, Description` CSV for Mouser's and Digi-Key's BOM upload pages. Only values in part-number categories (ICs, transistors, diodes, optoelectronics, transformers, relays) that look like one word of letters and digits are given as part numbers.

### 5. Telemetry (The Nervous System)

- **`feedback.py`**: User Loop.
  - **Role:** Appends the in-app feedback form to a Google Sheet. The frontend supplies the service account's credentials and decides how long to reuse the connection (the Streamlit app reads its secrets and reconnects hourly).

### 6. The API (The Front Door)

- **`api.py`**: What a frontend calls.
  - **Flow:** The frontend gathers each project into a `Project` (name, count, input method, and the text, link or `FileUpload`). It may read uploads and links ahead of time with `read_document`, to ask which build or whether to use OCR's parts, and passes the reading back on the `Project`. `generate_master_list` returns a `MasterList`: the merged parts, the parts plus the hardware kit, the totals with every input's errors, and each project as a `BuiltProject` (its final name and label, warnings, options and source document). The `MasterList` gives the shopping list, the PDF bundle and the build pack; the CSVs come from `exporters.py`.
  - **Invariants:** The backend never sees a UI framework's objects: uploads arrive as `FileUpload` (name and bytes). A multi-circuit document's build has no default, so `generate_master_list` raises `BuildNotChosenError` until each one is chosen. A reading passed in is never mutated, so the frontend can keep it between runs.
- **`app_state.py`**: The Streamlit app's session state (`ProjectSlot`, `Inputs`). Frontend state, kept out of the app script because Streamlit re-executes it on every rerun. Each `ProjectSlot` turns itself into a `Project`.

---

## 🧬 Data Flow

1. **Input:** The user uploads a PDF or CSV, pastes text or a link, or picks a preset; `strategies/` dispatches it.
1. **Parse:** `parser.py` and `layout.py` read the parts list; `builds.py` separates variants and options; `ocr.py` offers a reading of image-only lists.
1. **Verify:** `verification.py` re-reads the document and attaches review warnings.
1. **Classify & Normalize:** `classifier.py` tags each part; the grammar parses its value to a `Decimal`.
1. **Choose:** The frontend asks which build to make, and whether to use OCR's parts; `api.py` refuses to guess.
1. **Mutate:** `manager.py` merges projects and subtracts stock and parts included with the PCB.
1. **Enrich:** `sourcing.py` applies buffers and buying notes, and adds the hardware kit.
1. **Render:** `exporters.py` writes the CSVs; `pdf_generator.py` sorts by Z-height and draws the field manuals and sticker sheets.

## 🛠 Type Safety

We utilize `TypedDict` structures defined in `bom_lib/types.py` to enforce schema integrity across the pipeline.

```python
class PartData(TypedDict):
    qty: int
    val_qty: pint.Quantity[Any] | Decimal | None  # 10000 ohm for '10k'
    refs: list[str]  # ["R1", "R2"]
    sources: dict[str, list[str]]  # Traceability back to origin PDF
    included: int  # Of qty, how many come with the PCB
```
