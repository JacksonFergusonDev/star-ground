# ⚡ Star Ground (v2.3.0)

![Python Version](https://img.shields.io/badge/python-3.13-blue.svg)
[![Python Application CI](https://github.com/JacksonFergusonDev/star-ground/actions/workflows/python-app.yml/badge.svg)](https://github.com/JacksonFergusonDev/star-ground/actions/workflows/python-app.yml)
[![Docker](https://github.com/JacksonFergusonDev/star-ground/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/JacksonFergusonDev/star-ground/actions/workflows/docker-publish.yml)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**A deterministic dependency manager for physical hardware manufacturing.**

> **In circuit design, a "Star Ground" is the single reference point where all signal paths converge to eliminate noise.**
>
> **In manufacturing, this application serves the same function: it is the Single Source of Truth for your inventory, eliminating the "noise" of disorganized BOMs and supply chain drift.**

### 📡 The Mission: Deterministic Procurement
In software, dependency resolution is **deterministic**: `uv sync` guarantees the exact same environment every time. In hardware, procurement is currently stochastic: manual aggregation introduces human error, where a single forgotten $0.01 resistor causes a blocking failure weeks later.

*Star Ground* forces hardware logistics to behave like software. It rejects ambiguous inputs and treats physical inventory as a strict dependency tree. By enforcing a validated schema on unstructured data, it transforms procurement from **fragile manual aggregation** into a **deterministic data pipeline**.

**🚀 [Try the Live App](https://star-ground.streamlit.app/)**

<img src="assets/demo.gif" alt="Demo"
     style="display:block;margin-left:auto;margin-right:auto;width:70%;" />

> **Case Study:** This engine was utilized as the logistics backbone for [**Systems Audio Lab**](https://github.com/JacksonFergusonDev/systems-audio-lab), a project demonstrating the recursive engineering of physical instrumentation.

---

### ⚙️ Engineering Philosophy: Invariants over Inference

This system is designed to bridge the gap between **Software Precision** and **Hardware Chaos**. The architectural choices prioritize data integrity and human ergonomics over simple automation.

#### 1. Determinism over Probabilistic Models (Why not LLMs?)
Supply chains have zero tolerance for hallucination. A 10k resistor cannot be "inferred" as 1k.
* **The Decision:** Instead of using an LLM to parse PDFs, I implemented a **Hybrid Spatial Parser** (`src/bom_lib/parser.py`). It uses `pdfplumber` to extract table vectors (spatial analysis) and falls back to rigorous Regex pattern matching.
* **The Result:** 100% reproducible ingestion. The system fails loudly on ambiguity rather than guessing silently.

#### 2. Physical-Digital Isomorphism (Z-Height Sorting)
Most BOM tools sort lists alphabetically. **Star Ground** sorts by **Physical Z-Height**.
* **The Insight:** Efficient PCB assembly requires soldering low-profile components (Resistors, Diodes) before bulky ones (Electrolytic Capacitors, Switches) to keep the board flat on the workbench.
* **The Implementation:** The PDF generation engine enforces a topological sort order on the output artifacts, optimizing the *human operator's* runtime performance.

#### 3. Yield Management ("Nerd Economics")
* **The Problem:** The cost of a "Stockout" (halting work) is infinite relative to the cost of inventory.
* **The Algorithm:** The sourcing engine applies a **category-specific risk profile**. It automatically buffers cheap components (resistors rounded to nearest 10) while strictly calculating expensive ones (ICs), transforming purchasing logic from simple arithmetic into a risk-management strategy.

#### 4. Transparent Failure States (The "Loud" UI)
* **The Problem:** Most web apps swallow errors to "look clean," leaving users stranded when edge cases occur. In hardware, a silent failure (e.g., a parser dropping a line) results in a missing component and a failed build.
* **The Solution:** I implemented a **User-Facing Debug Console** and **Log Artifact Generation**.
    * The system strictly separates **"Residuals"** (noise) from **"Exceptions"** (logic failures).
    * Critical errors are never suppressed; they are surfaced in the UI with full stack traces.
    * Users can download a `debug.log` snapshot to attach to GitHub Issues, creating a deterministic feedback loop for bug reporting.

---

### 🍷 Feature Spotlight: The "Silicon Sommelier"

Beyond parsing, the engine functions as a domain expert system. It utilizes a lookup table of heuristic substitutions to suggest component upgrades based on audio engineering best practices.

* **Op-Amps:** Detects generic chips and suggests Hi-Fi alternatives (e.g., `TL072` → `OPA2134` for lower noise floor).
* **Fuzz Logic:** Automatically detects "Fuzz" topologies and injects Germanium transistors with specific "Positive Ground" warnings.
* **Texture Generation:** Maps clipping diodes to their sonic equivalents based on Forward Voltage ($V_f$) and Reverse Recovery Time ($t_{rr}$).

---

## 🚀 The Problem

Ordering parts for multiple analog circuits is error-prone.

* **Format Inconsistency:** Every BOM uses different spacing, tabs, and naming conventions.
* **Inventory Risk:** Buying parts you already own (waste) or forgetting a $0.01 resistor (shipping delay).
* **Assembly Chaos:** Mixing up parts between three different projects on the same workbench.

## 🛠 The Solution

This tool treats BOM parsing as a data reduction problem. It doesn't just read lines; it verifies them against a stateful inventory model.

### Key Features
1.  **Multi-Format Ingestion:**
    * **PDF Parsing:** Extracts tables from PedalPCB build docs using visual layout analysis (hybrid grid/text strategy).
    * **Smart Presets:** A hierarchical browser allowing filtering by Source (e.g., PedalPCB) and Category to load standard circuit definitions.
    * **URL Ingestion:** Fetch BOMs directly from websites like PedalPCB.
2.  **Inventory Logistics (Net Needs):**
    * Upload your current stock CSV.
    * The engine calculates `Net Need = max(0, BOM_Qty - Stock_Qty)`.
    * Safety buffers are only applied to the *deficit*, preventing over-ordering.
3.  **Manufacturing Outputs:**
    * **Field Manuals:** Generates Z-height sorted printable PDF checklists (Resistors → Sockets → Caps) for streamlined assembly.
    * **Sticker Sheets:** Generates Avery 5160 labels with condensed references (e.g., `R1-R4`) for part binning.
    * **Master Bundle:** Downloads a single ZIP containing all source docs, shopping lists, and manual PDFs.
4.  **Smart Normalization:**
    * Expands ranges automatically (`R1-R5` → `R1, R2, R3, R4, R5`).
    * Detects potentiometers by value (`B100k`) even if labeled non-standardly.
5.  **Observability & Debugging:**
    * **In-App Console:** Real-time visibility into the parsing kernel via a `st.session_state` log buffer.
    * **Error Taxonomy:** Visual distinction between "Partial Success" (warnings) and "Critical Failure" (errors), ensuring the user always knows the integrity of their data.

---

## 🧠 Engineering Architecture & Decisions

This system is designed to bridge the gap between **Software Precision** and **Hardware Chaos**. The architectural choices prioritize data integrity and human ergonomics over simple automation.

### 1. Physical-Digital Isomorphism (Z-Height Sorting)
Most BOM tools sort lists alphabetically or by Reference ID (`C1, C2, R1...`). **Star Ground** sorts by **Physical Z-Height**.
* **The Insight:** Efficient PCB assembly requires soldering low-profile components (Resistors/Diodes) before high-profile ones (Electrolytic Capacitors/Switches) to keep the board flat on the workbench.
* **The Implementation:** The PDF generation engine (`src/pdf_generator.py`) enforces a strict topological sort order on the output artifacts. The software explicitly optimizes the *human operator's* runtime performance, reducing context switching and physical instability during assembly.

### 2. Hybrid Spatial-Text Ingestion
PDFs are visual documents, not data structures. A standard text scraper loses the row/column relationships defined by the grid lines.
* **The Strategy:** I implemented a **Hybrid Parser** that utilizes `pdfplumber` to extract table vectors (spatial analysis) first.
* **The Fallback:** If the spatial grid is ambiguous, the system gracefully degrades to a deterministic Regex scanner. This "Defense in Depth" strategy allows the engine to digest everything from pristine digital exports to legacy documents without hallucinating data.

### 3. Yield Management ("Nerd Economics")
In small-batch manufacturing, the cost of a "Stockout" (halting work for a $0.05 part) is effectively infinite relative to the cost of inventory.
* **The Algorithm:** The sourcing engine applies a **category-specific risk profile** to the "Net Needs" calculation:
    * *Resistors:* **Round up to nearest 10** (Economy of scale; cheaper to buy 10 than 1).
    * *Discrete Silicon:* **+1 Safety Buffer** for Transistors/Oscillators (high risk of heat damage during soldering).
    * *ICs:* **Exact Count** (Protected by sockets, reducing risk of installation failure).
* This transforms the purchasing logic from simple arithmetic into a risk-management strategy.

### 4. SI Unit Normalization Engine
Electronic component values are notoriously inconsistent (`4k7`, `4.7k`, `4700`, `4,700R`). String matching fails here.
* **The Mechanism:** The ingestion layer (`src/bom_lib/utils.py`) acts as a recursive parser for SI prefixes (`p`, `n`, `u`, `k`, `M`). It normalizes all inputs to floating-point primitives ($4.7 \times 10^3$) before any aggregation occurs.
* **The Result:** `4k7` and `4700` are correctly aggregated as the exact same SKU, preventing duplicate orders that string-based parsers would miss.

---

## 🛡️ Verification & Validation

The reliability of the physical build depends entirely on the determinism of the data pipeline. To mitigate the "Logistical Entropy" of changing PDF formats, the system employs a multi-layered testing strategy:

### 1. Golden Master Regression (Snapshot Testing)
PDF parsing is inherently fragile. To ensure that updates to the parser do not silently break support for legacy formats, we utilize **Snapshot Testing**.
* **Methodology:** The test suite parses a library of "Golden Master" PDFs (real-world build docs).
* **Verification:** The resulting object model is serialized to JSON and diffed against a stored "Truth" file.
* **Outcome:** Any deviation in the parsing logic—even a single changed resistor value—triggers a CI failure, guaranteeing 100% backward compatibility.

### 2. Property-Based Testing (Hypothesis)
Standard unit tests only check the "happy path." We use the **Hypothesis** library to perform property-based testing.
* **Fuzzing:** The test runner generates thousands of semi-random inputs (malformed text, edge-case floats, Unicode injection) to "attack" the parser.
* **Invariant Checking:** Ensures that `calculate_net_needs()` remains mathematically sound (e.g., `Net_Need >= 0`) regardless of input chaos.

### 3. Headless Integration Testing
We utilize `Streamlit.AppTest` to run headless simulations of the user interface during CI.
* **Simulation:** The test runner instantiates the app kernel, mimics user clicks/uploads, and asserts the state of the dataframes.
* **Scope:** Verifies the full "Paste → Parse → Download" lifecycle without requiring a browser driver.

---

## 🔬 Tech Stack

-   **Python 3.13** - Core language (Strictly typed & pinned)
-   **uv** - Ultra-fast dependency management and locking
-   **Streamlit** - Interactive web interface and state management
-   **pdfplumber** - PDF table extraction and layout analysis
-   **fpdf2** - Programmatic PDF generation
-   **Docker** - Containerized runtime environment
-   **GitHub Actions** - Continuous Integration enforcing strict quality gates:
    -   *Linting:* Ruff
    -   *Type Safety:* Mypy
    -   *Snapshot Regression Testing:* PDF "Golden Master" verification (ensures parser stability across legacy build docs)
    -   *Property-Based Testing:* Hypothesis (Fuzzing component values to ensure mathematical invariants)
    -   *Integration Testing:* Streamlit AppTest (Headless simulation of the full "Paste-to-PDF" user lifecycle)
    -   *Delivery:* Auto-publishes Docker images to GHCR on release
    -   *Environment:* Ubuntu Latest

---

## 📦 Project Structure

```text
.
├── app.py                 <-- Interface: Streamlit Web App
├── assets/                <-- Static assets (images, demos)
├── Dockerfile             <-- Container configuration
├── examples/              <-- Output: Sample generated artifacts
│   └── Star_Ground_Artifacts/
│       ├── Field Manuals/
│       ├── Sticker Sheets/
│       ├── Source Documents/
│       ├── Shopping List.csv
│       └── My Inventory Updated.csv
├── raw_boms/              <-- Input: Source files for the Presets Library
│   ├── pedalpcb/
│   └── tayda/
├── src/                   <-- Application Core
│   ├── bom_lib/           <-- Domain Logic Package
│   │   ├── __init__.py    <-- Public API exposure
│   │   ├── classifier.py  <-- Logic: Component identification heuristics
│   │   ├── constants.py   <-- Data: Static lookups and regex patterns
│   │   ├── manager.py     <-- Logic: Inventory mutation & net needs calculation
│   │   ├── parser.py      <-- Logic: PDF/CSV ingestion engines
│   │   ├── presets.py     <-- Data: Library of known pedal circuits
│   │   ├── sourcing.py    <-- Logic: Purchasing rules & hardware injection
│   │   ├── types.py       <-- Data: Type definitions (TypedDicts)
│   │   └── utils.py       <-- Logic: String parsing & normalization
│   ├── exporters.py       <-- Logic: CSV/Excel generation
│   ├── feedback.py        <-- Logic: Google Sheets API integration
│   └── pdf_generator.py   <-- Output: Field Manuals & Sticker Sheets
├── tests/                 <-- QA Suite
│   ├── samples/           <-- Real-world PDF/Text inputs for regression
│   └── snapshots/         <-- Golden Master JSONs for PDF testing
├── tools/                 <-- Developer Utilities
│   └── generate_presets.py
├── CONTRIBUTING.md        <-- Dev guide
├── ROADMAP.md             <-- Technical architectural plans
├── pyproject.toml         <-- Project metadata & tool config (Ruff/Mypy/Pytest)
├── uv.lock                <-- Exact dependency tree (Deterministic builds)
└── requirements.txt       <-- Deployment: Generated via uv for Streamlit Cloud
```

---

## 🚀 Quick Start

### Option 1: Web Interface (Live App)
**🌐 [Use the hosted app here](https://star-ground.streamlit.app/)**

### Option 2: Run via Docker
You can pull the pre-built image directly from the GitHub Container Registry without building it yourself.

```bash
# Run latest stable release
docker run -p 8501:8501 ghcr.io/jacksonfergusondev/star-ground:latest
```

Or build from source:

```bash
docker build -t star-ground .
docker run -p 8501:8501 star-ground
```

### Option 3: Local Development

This project uses **uv** for dependency management.

```bash
# 1. Clone & Enter
git clone https://github.com/JacksonFergusonDev/star-ground.git
cd star-ground

# 2. Install Dependencies (Creates virtualenv automatically)
uv sync

# 3. Run App
uv run streamlit run app.py
```

---

## 🔭 Roadmap

We are aggressively moving from a simple regex script to a context-aware physics engine.

**Key Upcoming Initiatives:**
* **Architecture:** Migrating to a Strategy Pattern and Context-Free Grammars for parsing.
* **Intelligence:** Topology inference (detecting "Fuzz" vs "Delay" circuits based on component clusters).
* **Finance:** Real-time pricing integration (Octopart/DigiKey) and volume arbitrage.

For the detailed technical breakdown and milestones, see **[ROADMAP.md](ROADMAP.md)**.

---

## 🤝 Contributing

We welcome contributions! Please see **[CONTRIBUTING.md](CONTRIBUTING.md)** for details on how to set up the dev environment, run the snapshot tests, and submit PRs.

---

## 📧 Contact

**Jackson Ferguson**

-   **GitHub:** [@JacksonFergusonDev](https://github.com/JacksonFergusonDev)
-   **LinkedIn:** [Jackson Ferguson](https://www.linkedin.com/in/jackson--ferguson/)
-   **Email:** jackson.ferguson0@gmail.com

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
