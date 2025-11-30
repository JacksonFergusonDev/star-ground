# 🎸 Guitar Pedal BOM Manager

![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**Stop manually typing parts into Mouser. Paste your list, get a verified shopping cart.**

The **Pedal BOM Manager** is a "Smart Clipboard" for guitar pedal builders. It takes messy, unformatted component lists from DIY sites, cleans them up, and tells you exactly what to buy—preventing shipping delays caused by missed parts.

**🚀 [Try the Live App](https://pedal-bom-manager.streamlit.app/)**

![Demo](assets/demo.gif)

### ⚙️ Engineering Overview
**A robust Python tool designed to automate the chaotic process of sourcing electronic components.**

It aggregates messy, inconsistent Bill of Materials (BOM) text from various sources, cleans the data using Regex, performs statistical verification to ensure data integrity, and calculates a "Smart Shopping List" based on safety stock principles.

> **Note:** This repository contains the *software tooling* developed to support a larger hardware engineering project. A companion repository featuring the circuit builds, schematics, and spectral analysis of the pedals themselves is currently in development.

---

### 🍷 Feature Spotlight: The "Silicon Sommelier"

Beyond parsing, the engine functions as a domain expert system. It utilizes a lookup table of heuristic substitutions (`IC_ALTS`) to suggest component upgrades based on audio engineering best practices. If the BOM calls for a generic `TL072`, the system detects the signature and appends "flavor notes" to the CSV:

* "Try OPA2134 for lower noise floor"
* "Try JRC4558 for vintage clipping specs"

---

## 🚀 The Problem

Ordering parts for multiple analog circuits is error-prone.

* **Format Inconsistency:** Every BOM uses different spacing, tabs, and naming conventions.
* **Inventory Risk:** Forgetting a single $0.01 resistor costs $15.00 in shipping.
* **Obsolete Parts:** Modern builds often require SMD adaptations (e.g., JFETs like 2N5457) that are easily overlooked.

## 🛠 The Solution

This tool treats BOM parsing as a data reduction problem. It doesn't just read lines; it verifies them.

### Key Features
1. **Multi-Format Ingestion:** Accepts raw text pastes, CSV uploads, or batch processing of text files.
2. **Smart Normalization:**
   * Expands ranges automatically (`R1-R5` → `R1, R2, R3, R4, R5`).
   * Handles "Lazy CSV" formats (comma-separated text).
   * Detects potentiometers by value (`B100k`) even if labeled non-standardly.
3. **Residual Analysis:** A verification step inspired by astrophysical data reduction. The script logs every line it fails to parse and scans them for "suspicious" content (numbers/keywords).
4. **Logic Injection:** The parser acts as a domain expert. It automatically adds:
   * SMD Adapters for obsolete JFETs.
   * IC Sockets for sensitive chips.
5. **"Nerd Economics":** Automatically calculates purchase quantities based on component risk and price.
   * *Resistors:* Buffer +5 (Minimum 10).
   * *ICs:* Buffer +1 (Backup).
6. **The "Sommelier" Engine:** Recognizes common audio chips (e.g., TL072) and suggests "flavor mods" (e.g., "Try OPA2134 for Hi-Fi audio") in the shopping notes.

---

## 🎯 Compatible Sources

**Designed for:**
* [Tayda Electronics](https://www.taydaelectronics.com/) - Primary BOM format target

**Compatible with:**
* [PedalPCB](https://www.pedalpcb.com/)
* [Aion FX](https://aionfx.com/)
* [Heavy Metal FX](https://heavymetalfx.com/)

**Planned Support:**
* [GuitarPCB](https://guitarpcb.com/)
* [God City Instruments](https://www.godcityinstruments.com/)

> **Note:** The parser handles multiple BOM formats, but if you encounter parsing issues with a specific source, please [open an issue](https://github.com/yourusername/pedal-bom-manager/issues) with a sample BOM.

---

## 🧠 Engineering Decisions

This project was built to solve a specific reliability problem. Here is the reasoning behind the architectural choices:

### 1. Deterministic Regex over LLMs
While it might be easier to pass BOMs to an LLM (like ChatGPT), non-deterministic outputs are unacceptable for procurement. A hallucinated quantity or part number results in a failed hardware build.
* **Decision:** I implemented a **deterministic Regex parser** to ensure 100% repeatability.
* **Implementation:** The strict pattern matching isolates designators and values, ignoring variable whitespace and human-written descriptions.

### 2. Residual Analysis (Self-Verification)
Drawing from astrophysical data reduction techniques, it is critical to analyze "residuals" (data not fitted by the model) to ensure no signal is lost.
* **The Mechanism:** The script logs every line of text that *fails* the regex match.
* **The Safety Net:** It scans these residuals for "suspicious" content (numbers, keywords like "uF"). If a component is skipped due to formatting errors, the system alerts the user immediately in the terminal.

### 3. Logic Injection for Obsolete Parts
Modern DIY electronics often rely on parts that are now only available in Surface Mount (SMD) formats (e.g., the 2N5457 JFET).
* **The Risk:** Beginners often buy the SMD chip but forget the conversion board, stalling the project.
* **The Solution:** The parser acts as a domain expert. It detects specific obsolete part numbers and **injects** the required adapter hardware into the shopping list automatically.

### 4. Heuristic Safety Stock ("Nerd Economics")
In hardware prototyping, the cost of downtime exceeds the cost of inventory.
* **Decision:** I implemented a **Yield Management Algorithm** that adjusts purchase quantities based on component risk vs. cost.
* **Logic:**
    * **High Risk / Low Cost:** Resistors get a +10 buffer (Cost: ~$0.10. Benefit: Prevents $15 shipping fees for lost parts).
    * **Critical Silicon:** ICs get a +1 buffer (socketing protection).
    * **Low Risk / High Cost:** Potentiometers and Switches get a zero buffer.

---

## 🔬 Tech Stack

- **Python 3.11+** - Core language
- **Regex** - Pattern matching engine for component extraction
- **Streamlit** - Interactive web interface
- **CSV/Markdown** - Structured output formats

---

## 📋 Example: Raw BOM → Clean Output

**Input (messy text):**
```text
R1    100k
C1 10uF
R2      47k  1/4W
IC1     TL072
```

**Output ([shopping_list.csv](examples/shopping_list.csv)):**

| Component | Value | Qty | Buy Qty | Notes |
|-----------|-------|-----|---------|-------|
| R1        | 100kΩ | 1   | 10      | Buffer: +5 (Minimum 10) |
| R2        | 47kΩ  | 1   | 10      | Buffer: +5 (Minimum 10) |
| C1        | 10µF  | 1   | 4       | Buffer: +3 |
| IC1       | TL072 | 1   | 2       | Buffer: +1 (Backup) |
| Socket    | DIP-8 | 1   | 2       | Auto-injected for IC1 |

---

## 📦 Project Structure
```text
pedal-bom-manager/
├── .gitignore
├── LICENSE                <-- MIT License
├── README.md
├── app.py                 <-- Interface: Streamlit Web App
├── cli.py                 <-- Interface: Command Line Tool
├── pytest.ini             <-- Testing: Pytest configuration
├── requirements.txt       <-- Dependencies
├── assets/
│   └── demo.gif           <-- Demo: Visual walkthrough
├── data/                  <-- Input: Sample BOM files
│   ├── big_muff.txt
│   ├── bluesbreaker.txt
│   ├── dr_q.txt
│   └── rat.txt
├── examples/              <-- Output: Sample generated files
│   ├── shopping_list.csv
│   └── checklist.md
├── src/
│   └── bom_lib.py         <-- Logic: Regex engine, verification, and buying rules
└── tests/
    └── test_parser.py     <-- Testing: Parser unit tests
```

**Key Files:**
- **[app.py](app.py)** - Streamlit web interface
- **[cli.py](cli.py)** - Command-line batch processor
- **[src/bom_lib.py](src/bom_lib.py)** - Core parsing and verification logic
- **[examples/master_shopping_list.csv](examples/shopping_list.csv)** - Sample output (CSV)
- **[examples/shopping_checklist.md](examples/checklist.md)** - Sample output (Markdown)

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+

### Installation
```bash
# 1. Clone the repository
git clone https://github.com/JacksonFergusonDev/pedal-bom-manager.git

# 2. Navigate to the project directory
cd pedal-bom-manager

# 3. Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt
```

### Usage

#### Option 1: Web Interface (Live App)

**🌐 [Use the hosted app here](https://pedal-bom-manager.streamlit.app/)** - No installation required!

1. Select the **Paste Text** tab to copy-paste from a forum
2. Select the **Upload CSV** tab to process KiCad/Excel exports
3. Download your consolidated Shopping List

#### Option 2: Run Locally (Streamlit)
```bash
streamlit run app.py
```

Then paste your BOM text into the window and download your CSV.

#### Option 3: Command Line Tool (CLI)

For batch processing local files:

1. Place your text files (e.g., `big_muff.txt`) inside the `/data` folder
2. Run the script:
```bash
python cli.py
```
3. Check the terminal for the Verification Report and find your generated files in the working directory

---

## 📊 Sample Output (Verification Report)

When running the CLI, the tool provides a detailed report on the integrity of the parsing process:
```text
📂 Reading 4 files from 'data'...
   ok: rat.txt
   ok: bluesbreaker.txt
   ok: dr_q.txt
   ok: big_muff.txt

--- Stats ---
Lines: 183 | Parts: 151

⚠️  Skipped 1 lines (might be important):
   ? Transistor equivalents:  2N5089, BC549C, BC239 , 2N5210...

💡 Logic Notes:
   ⚠️  SMD ADAPTERS: Added for MMBF5457. Check if your PCB has SOT-23 pads first.
   ℹ️  IC SOCKETS: Added sockets for chips. Optional but recommended.

✅ CSV: output/shopping_list.csv
✅ MD:  output/checklist.md

Done.
```

---

## 📝 Generated Artifacts

The tool produces two files:

1.  **[shopping_list.csv](examples/shopping_list.csv)**: A clean spreadsheet sorted by component priority (PCBs -> ICs -> Passives). Includes a "Notes" column with auto-generated warnings.
2.  **[checklist.md](examples/checklist.md)**: A GitHub-ready markdown table with checkboxes for tracking your order status.

Sample outputs can be found in the `/examples` directory.

---

## 🧪 Testing & QA

Reliability is critical when ordering hardware components. This project uses a comprehensive testing suite to ensure the regex engine handles malformed inputs without crashing.

**The Test Suite includes:**
* **Unit Tests:** Verifies core parsing logic against known BOM formats.
* **Logic Verification:** Ensures auto-injection rules (e.g., SMD adapters) trigger correctly.
* **Property-Based Testing (Fuzzing):** Uses the `Hypothesis` library to generate thousands of random, malformed text inputs to stress-test the parser for unhandled exceptions.

---

# 🔭 Future Trajectory: Roadmap

This project is evolving from a static text parser into a dynamic procurement optimization engine. Development is divided into three main areas: infrastructure hardening, signal extraction, and quantitative financial logic.

## 1. Infrastructure & Reliability

**Goal:** Eliminate environment drift and ensure deterministic execution.

* **Dockerization:** Dockerfile and docker-compose workflow to isolate dependencies (Python 3.11, Streamlit) and guarantee a reproducible runtime environment across different machines.
* **CI/CD Pipeline:** GitHub Actions to automate testing:
  * **Linting:** Strict `ruff` enforcement for PEP-8 compliance.
  * **Unit Tests:** Automated `pytest` execution on every commit.
  * **Type Checking:** Static analysis via `mypy`.

## 2. Advanced Data Analysis

**Goal:** Quantify parsing efficacy using techniques inspired by observational data reduction.

* **Residual Analysis Report:** Analytics module to visualize the "Signal-to-Noise" ratio (successfully parsed components vs. rejected lines).
* **Confidence Intervals:** Regex engine assigns a confidence score $C \in [0.0, 1.0]$ to each parsed component.
  * **High Confidence ($C \approx 1.0$):** Strict Designator Matches (e.g., R1, C4).
  * **Low Confidence ($C < 0.5$):** Heuristic Value Matches (e.g., inferring Potentiometer solely from value "B100k").
* **Ingest Spectrogram:** Visualization of ingest efficiency per source file, allowing users to identify "noisy" input data formats at a glance.

## 3. Topology Inference

**Goal:** Expand the domain expert system to analyze BOM composition and infer circuit topology.

* **Pattern Recognition Engine:** Boolean masks to detect specific component clusters.
  * **Example:** Germanium Diode + PNP Transistor implies "Vintage Fuzz Topology" (triggers Positive Ground warning).
  * **Example:** PT2399 + Voltage Regulator implies "Digital Delay" (triggers noise filtering recommendation).

## 4. Financial Optimization

**Goal:** Transition from static buffers to probabilistic risk modeling.

* **Live Pricing Integration:** Octopart or DigiKey APIs to query real-time unit prices.
* **Volume Arbitrage:** Algorithm to calculate break-even points on price breaks. (e.g., Is it cheaper to buy 100 resistors at $0.008 than 40 at $0.05?)
* **Probabilistic Inventory Modeling:** Refactoring "Nerd Economics" into a risk-adjusted model that calculates optimal order quantities based on component volatility. High-loss probability items (small SMD parts) trigger larger buffer coefficients than mechanically robust components.

## 5. Automated Documentation Pipeline

**Goal:** Upgrade the human-readable output to "Publication Quality" while retaining the CSV as the accessible data standard.

* **LaTeX Integration:** Human-readable output transitions from simple Markdown to compiled LaTeX PDF. This acts as a "Field Manual" for the build process, replacing the basic checklist.
* **Persistent CSV:** The CSV output remains the lightweight, machine-readable standard for quick imports into vendor carts or Excel.
* **The Last Mile:** Formatted PDF grids for Avery stickers (Designator + Value) to streamline the physical binning process.

---

## 🤝 Contributing

Found a bug? Have an idea for improvement? Contributions are welcome!

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📧 Contact

**Jackson Ferguson**

- **GitHub:** [@JacksonFergusonDev](https://github.com/JacksonFergusonDev)
- **LinkedIn:** [Jackson Ferguson](https://www.linkedin.com/in/jackson--ferguson/)
- **Email:** jackson.ferguson0@gmail.com

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.