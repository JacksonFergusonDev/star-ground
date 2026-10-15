# 🏗️ Architecture: The Signal Processing Pipeline

The `src/` directory is structured as a unidirectional data pipeline. Data flows from **High Entropy** (Raw PDFs/Text) to **Low Entropy** (Structured Objects, Manufacturing Artifacts).

The system enforces strict separation between **Ingestion**, **Normalization**, **State Management**, and **Presentation**.

---

## 📂 Module Map

### 1. Ingestion Layer (The Senses)

- **`bom_lib/parser.py`**: The entry point for dynamic data.
  - **Strategy:** Implements a **Hybrid Strategy**. It maps ruled PDF tables using `pdfplumber`, then runs a column-aware layout pass to recover everything the tables missed (or everything, for borderless multi-column parts lists).
- **`bom_lib/layout.py`**: The layout reader.
  - **Role:** Groups positioned words into lines and *cells* (split at column-sized gaps), then pairs each designator cell with its value cell. This keeps multi-word values whole (`3mm Red LED`, `10u (25V or higher)`) without bleeding into the neighboring column.
- **`bom_lib/designators.py`**: The designator grammar.
  - **Role:** Decides what is a reference designator (`R1A`, `IC_A`, `LEDA`, `CLR1`) and what category its prefix implies; anything else is a named control classified by its value. Shared by the classifier and the layout reader so they cannot disagree.
- **`bom_lib/builds.py`**: The build planner.
  - **Role:** Splits extracted entries into common parts, *variants* (separate circuits in one document: Muffin Fuzz's versions, a Germanium and a Silicon list, a "Cleft mod") and *options* (interchangeable parts: through-hole or SMD). A multi-circuit result has no default build: `ParseResult.select()` must be called with the builder's choice, and the app asks for it.
- **`bom_lib/verification.py`**: The self-checks.
  - **Role:** Independently re-reads the document and reports every place the output may disagree with it (unextracted designators, conflicting duplicates, category contradictions, unparseable values, bill-of-materials mismatches). The invariant: the parser may be wrong, but never *silently*.
- **`bom_lib/presets.py`**: The entry point for static data.
  - **Role:** Acts as the "Reference Library." It allows users to load pre-verified BOMs for known circuits (e.g., PedalPCB projects) without parsing a file.
- **`bom_lib/_presets_data.py`**: The static database.
  - **Note:** This file is **auto-generated** by `tools/generate_presets.py`. It contains the raw text blobs of valid BOMs to ensure the presets are immutable and version-controlled.

### 2. Classification & Normalization (The Filter)

- **`bom_lib/classifier.py`**: The identification engine.
  - **Heuristics:** Uses reference designators (`R1`, `U1`) and value signatures to categorize components. It determines if `C1` is a Ceramic Disc (low profile) or an Electrolytic (high profile) based on value thresholds.
- **`bom_lib/utils.py`**: The recursive SI parser.
  - **Role:** The "Unit Enforcer." It converts chaotic strings (`4k7`, `4.7k`, `4,700R`) into floating-point primitives ($4.7 \times 10^3$) before storage, ensuring mathematical uniqueness.
- **`bom_lib/constants.py`**: The knowledge base.
  - **Content:** Stores the "Silicon Sommelier" dictionaries (audio-grade substitutions), Regex patterns, and SI multipliers. This separates *configuration* from *logic*.
- **`bom_lib/types.py`**: The schema definitions.
  - **Role:** Defines `TypedDict` structures (`PartData`, `StatsDict`) to enforce strict typing across the pipeline, preventing "stringly typed" data errors.

### 3. Logic Kernel (The Brain)

- **`bom_lib/manager.py`**: Pure Python state management.
  - **Role:** The "Controller." It calculates `Net Needs = BOM - Stock` and handles the merging of multiple projects into a single master list.

- **`bom_lib/sourcing.py`**: Yield Management ("Nerd Economics").
  - **Logic:** Applies category-specific risk profiles:
    - *Resistors:* Round up to nearest 10 (Economy of Scale).
    - *ICs:* Exact count (High cost).
    - *Hardware Injection:* Automatically injects off-BOM parts (Jacks, Switches) based on the circuit topology (e.g., detecting "Fuzz" vs "Delay").

### 4. Output Layer (The Hands)

- **`pdf_generator.py`**: Manufacturing Artifacts.
  - **Feature:** **Z-Height Sorting**. It reorders the build instructions so the operator installs components from shortest (Resistors) to tallest (Capacitors), optimizing mechanical stability.

- **`exporters.py`**: Digital serialization.
  - **Feature:** Generates CSVs and Excel-compatible strings, injecting `=HYPERLINK()` formulas for one-click purchasing.

### 5. Telemetry (The Nervous System)

- **`feedback.py`**: User Loop.
  - **Role:** Connects to Google Sheets to log user feedback and bug reports. It uses a connection pool with `st.cache_resource` to handle high-concurrency reporting.

---

## 🧬 Data Flow

1. **Input:** User uploads a PDF OR selects a Preset (`presets.py`).
1. **Parse:** `parser.py` extracts text $\rightarrow$ `classifier.py` tags it.
1. **Normalize:** `utils.py` converts strings to Floats using `constants.py`.
1. **Mutate:** `manager.py` aggregates quantities and subtracts Stock.
1. **Enrich:** `sourcing.py` injects missing hardware (Jacks/Footswitches).
1. **Render:** `pdf_generator.py` sorts by Z-Height and draws the PDF.

## 🛠 Type Safety

We utilize `TypedDict` structures defined in `bom_lib/types.py` to enforce schema integrity across the pipeline.

```python
class PartData(TypedDict):
    qty: int
    refs: list[str]  # ["R1", "R2"]
    sources: dict[str, list[str]]  # Traceability back to origin PDF
```
