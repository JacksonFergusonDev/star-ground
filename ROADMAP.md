# 🗺️ Project Roadmap: Electronics BOM Parsing Engine

This document outlines the development trajectory for Star Ground. The roadmap is divided into **Architectural Milestones** (improving the core parsing engine) and **Feature Expansions** (new capabilities for the end user).

---

## 🏗️ Architectural Evolution

We are moving from a regex-based script to a grammar-based physics engine.

### Milestone 1: Architectural Decoupling (The Strategy Pattern)
The goal is to move away from linear script execution and implement a dispatch system that respects the Open/Closed Principle.
* **Define Abstract Base Classes (ABCs):** Implement a `BOMParserStrategy` interface that defines `parse()` and `can_handle()` methods.
* **Concrete Implementations:** Create specific classes for `PDFParserStrategy`, `CSVParserStrategy`, and `ManualInputStrategy`.
* **Context Manager:** Build a `BOMParserContext` to inspect file signatures and instantiate the correct strategy at runtime.

### Milestone 2: Linguistic Upgrade (From Regex to Grammars)
Moving from Regular Languages to Context-Free Languages to handle the structural complexity of non-passive components.
* **Integrate Parser Combinators:** Use `parsy` or `pyparsing` to define atomic primitives like `digit`, `multiplier`, and `unit`.
* **Recursive RKM Parsing:** Replace the RKM regex with a composite parser that handles variable decimal placement (e.g., $4k7$, $R22$) without greedy-matching false positives.
* **Functional Transformation:** Use the `.map()` method within your parsers to normalize values during the extraction phase rather than post-processing.

### Milestone 3: The Physics Layer (Precision and Units)
Eliminating floating-point artifacts and manual unit mapping.
* **Numerical Precision:** Replace all `float` usage with the `Decimal` type to ensure exact precision across the $10^{18}$ dynamic range found in electronics.
* **Unit Integration:** Use the `Pint` library to handle unit aliases and prefixes. This allows the system to treat $10uF$, $10\mu F$, and $10 \text{ microfarads}$ as identical objects.
* **Dimensional Analysis:** Implement checks to prevent comparing orthogonal units (e.g., checking a voltage rating against a resistance value).

### Milestone 4: PDF Vision and Layout Analysis
PDFs possess no inherent tabular structure, so we must reverse-engineer the visual intent of the document.
* **Heuristic Analyzer:** Implement a pre-parsing scan to detect vector line objects.
* **Lattice vs. Stream:** If grid lines are detected, invoke `Camelot` or `pdfplumber` with a `vertical_strategy="lines"`. If borderless, use whitespace "river" analysis.
* **Entity Anchoring:** Build a row-merging algorithm that glues "orphan text" (multi-line descriptions) back to their parent "anchor row" based on Y-coordinate proximity.

### Milestone 5: Fault Tolerance (The Safety Layer)
A single malformed row must not halt the entire data pipeline.
* **Dead Letter Queue (DLQ):** Implement a per-row try/catch block that diverts failed parses into a `Failure Stream`.
* **Verification Layer:** Validate all extracted passives against the IEC 60063 E-Series (E24, E96).
* **Error Reporting:** Ensure the final output includes a success list and a structured DLQ report for targeted human remediation.

---

## 🔮 Feature Expansion & Expert Systems

Expanding the domain knowledge of the system to provide "Senior Engineer" level feedback.

### 1. Topology Inference
**Goal:** Expand the domain expert system to analyze BOM composition and infer circuit topology.
* **Pattern Recognition Engine:** Boolean masks to detect specific component clusters.
* **Examples:**
    * *Germanium Diode + PNP Transistor* → Implies "Vintage Fuzz Topology" (Triggers Positive Ground warning).
    * *PT2399 + Voltage Regulator* → Implies "Digital Delay" (Triggers noise filtering recommendation).

### 2. Financial Optimization
**Goal:** Transition from static buffers to probabilistic risk modeling.
* **Live Pricing Integration:** Octopart or DigiKey APIs to query real-time unit prices.
* **Volume Arbitrage:** Algorithm to calculate break-even points on price breaks. (e.g., Is it cheaper to buy 100 resistors at $0.008 than 40 at $0.05?)
* **Probabilistic Inventory Modeling:** Refactoring "Nerd Economics" into a risk-adjusted model that calculates optimal order quantities based on component volatility. High-loss probability items (small SMD parts) trigger larger buffer coefficients than mechanically robust components.

### 3. Advanced Data Analysis
**Goal:** Quantify parsing efficacy using techniques inspired by observational data reduction.
* **Residual Analysis Report:** Analytics module to visualize the "Signal-to-Noise" ratio (successfully parsed components vs. rejected lines).
* **Confidence Intervals:** Regex engine assigns a confidence score $C \in [0.0, 1.0]$ to each parsed component.
    * *High Confidence ($C \approx 1.0$):* Strict Designator Matches (e.g., R1, C4).
    * *Low Confidence ($C < 0.5$):* Heuristic Value Matches (e.g., inferring Potentiometer solely from value "B100k").
* **Ingest Spectrogram:** Visualization of ingest efficiency per source file, allowing users to identify "noisy" input data formats at a glance.