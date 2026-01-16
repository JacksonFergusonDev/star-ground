# 🎸 Guitar Pedal BOM Manager (v2.0.0)

![Python Version](https://img.shields.io/badge/python-3.13-blue.svg)
[![Python Application CI](https://github.com/JacksonFergusonDev/pedal-bom-manager/actions/workflows/python-app.yml/badge.svg)](https://github.com/JacksonFergusonDev/pedal-bom-manager/actions/workflows/python-app.yml)
[![Docker](https://github.com/JacksonFergusonDev/pedal-bom-manager/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/JacksonFergusonDev/pedal-bom-manager/actions/workflows/docker-publish.yml)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**Stop manually typing parts into Tayda. Paste your list, subtract your inventory, and print your build docs.**

The **Pedal BOM Manager** is a full-stack logistics engine for guitar pedal builders. It ingests messy component lists (Text, CSV, PDF), normalizes the data, subtracts your existing inventory to calculate "Net Needs," and generates a complete manufacturing bundle—including shopping lists, assembly field manuals, and binning labels.

**🚀 [Try the Live App](https://pedal-bom-manager.streamlit.app/)**

![Demo](assets/demo.gif)

### ⚙️ Engineering Overview
**A robust Python tool designed to automate the chaotic process of sourcing and assembling electronics.**

It aggregates data from inconsistent Bill of Materials (BOM) sources, performs statistical verification to ensure data integrity, and applies "Nerd Economics" (heuristic safety buffering) to generate smart purchasing lists.

v2.0.0 introduces a **lossless data structure** that tracks component provenance across multiple projects simultaneously, enabling batch-building logistics (e.g., "Build 2x Big Muffs and 1x Tube Screamer").

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
    * **Presets Library:** Hierarchical selection of standard circuits (e.g., "Parentheses Fuzz - PedalPCB").
    * **URL Ingestion:** Fetch BOMs directly from GitHub or raw text links.
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

---

## 🧠 Engineering Decisions

This project was built to solve a specific reliability problem. Here is the reasoning behind the architectural choices:

### 1. Deterministic Regex over LLMs
While it might be easier to pass BOMs to an LLM, non-deterministic outputs are unacceptable for procurement. A hallucinated quantity results in a failed hardware build.
* **Decision:** I implemented a **deterministic Regex parser** with a "Hybrid Strategy" for PDFs (Table Extraction + Regex Fallback) to ensure 100% repeatability.

### 2. Lossless Data Structure
v1.0.0 used simple counters. v2.0.0 implements a `PartData` TypedDict that tracks specific references per source.
* **The Benefit:** We can merge 3 different projects into one master list, but still generate individual "Field Manuals" for each project because the provenance of every `R1` is preserved.

### 3. Snapshot Testing
To manage the fragility of PDF parsing, the test suite uses **Snapshot Testing**.
* **The Mechanism:** The parser runs against a library of "Golden Master" PDFs. The output is compared against stored JSON snapshots. Any deviation in parsing logic triggers a regression failure, ensuring that supporting a new PDF format doesn't break support for older ones.

### 4. Heuristic Safety Stock ("Nerd Economics")
In hardware prototyping, the cost of downtime exceeds the cost of inventory.
* **Decision:** I implemented a **Yield Management Algorithm** that adjusts purchase quantities based on component risk vs. cost.
    * *High Risk / Low Cost:* Resistors get a +10 buffer.
    * *Critical Silicon:* ICs get a +1 buffer (socketing protection).
    * *Low Risk / High Cost:* Potentiometers and Switches get a zero buffer.

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
    -   *Unit Testing:* Pytest
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
│   └── Pedal_Build_Pack_Complete/
│       ├── Field Manuals/
│       ├── Sticker Sheets/
│       ├── Source Documents/
│       ├── Pedal Shopping List.csv
│       └── My Inventory Updated.csv
├── raw_boms/              <-- Input: Source files for the Presets Library
│   ├── pedalpcb/
│   └── tayda/
├── src/                   <-- Application Core
│   ├── bom_lib.py         <-- Logic: Regex engine & buying rules
│   ├── constants.py       <-- Data: Static lookups and regex patterns
│   ├── exporters.py       <-- Logic: CSV/Excel generation
│   ├── feedback.py        <-- Logic: Google Sheets API integration
│   ├── pdf_generator.py   <-- Output: Field Manuals & Sticker Sheets
│   └── presets.py         <-- Data: Library of known pedal circuits
├── tests/                 <-- QA Suite
│   ├── samples/           <-- Real-world PDF/Text inputs for regression
│   └── snapshots/         <-- Golden Master JSONs for PDF testing
├── tools/                 <-- Developer Utilities
│   └── generate_presets.py
├── CONTRIBUTING.md        <-- Dev guide
├── ROADMAP.md             <-- Technical architectural plans
├── pyproject.toml         <-- Project metadata & tool config (Ruff/Mypy/Pytest)
├── uv.lock                <-- Exact dependency tree (Deterministic builds)
└── requirements.txt       <-- Python dependencies
```

---

## 🚀 Quick Start

### Option 1: Web Interface (Live App)
**🌐 [Use the hosted app here](https://pedal-bom-manager.streamlit.app/)**

### Option 2: Run via Docker
You can pull the pre-built image directly from the GitHub Container Registry without building it yourself.

```bash
# Run latest stable release
docker run -p 8501:8501 ghcr.io/jacksonfergusondev/pedal-bom-manager:latest
```

Or build from source:

```bash
docker build -t pedal-bom-manager .
docker run -p 8501:8501 pedal-bom-manager
```

### Option 3: Local Development

This project uses **uv** for dependency management.

```bash
# 1. Clone & Enter
git clone https://github.com/JacksonFergusonDev/pedal-bom-manager.git
cd pedal-bom-manager

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