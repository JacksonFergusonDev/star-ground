# Known Parser Gaps

## Pythagoras v3

- **Dielectric Specification Data Loss:** The regex engine greedily grouped all 1µF capacitors into a single generic bucket, dropping the `MLCC` (Multi-Layer Ceramic Capacitor) designation explicitly required for C2, C3, C6, C8, and C10. It completely failed to distinguish C9, which omits the MLCC tag and requires a standard film capacitor footprint.
- **Truncated Switch States:** The parser captured the base `SPDT` switch type for SW1 but dropped the parenthetical `(On/Off/On)`. Missing this center-off state breaks the hardware routing required for the FV-1 DSP algorithm selection.
- **Hallucinated DIP Sockets for SMD Components:** The parser's current classification logic aggressively mapped ICs to standard DIP sockets, incorrectly assigning one to IC3. The FV-1 DSP is strictly a surface-mount SOP-28 package, rendering a standard through-hole socket useless.
- **Ignored Global Constraints:** The parsing routine missed the footer documentation explicitly excluding standard offboard components (enclosures, footswitches, jacks), which risks redundant procurement if the BOM is piped directly to a fulfillment API.

## Distortr

- **Omitted Component:** Resistor R13 (330Ω)
- **Omitted Component:** Resistor R18 (82Ω)
- **Omitted Component:** Switch SUBS (SPDT On - Off - On)
- **Miscategorization:** Switch GAIN (SPDT On - On) was sorted into the `Potentiometers` category rather than a dedicated `Switches` category.
- **Value Extraction Error:** Diode D1 was extracted using its schematic label (`9v1`) instead of the specific component part number listed in the Bill of Materials (`1N4739A`).

## Raincoat

- **Missed 220Ω Resistor (`R19`):** The regex engine extracted `R6` (qty: 1) but failed to capture `R19`, missing the BOM requirement of 2.
- **Missed 390Ω Resistors (`R10`, `R14`):** Logged a quantity of 1 (`R5`), dropping the other two required by the BOM (total qty: 3).
- **Incorrect Diode Assignment (`D3`):** The parser greedily matched the "N/A" value from the Page 1 component table, failing to reconcile with the schematic which explicitly assigns it as a `1N4001` diode.

## Ungula

- **Missing Resistors:** The parser failed to extract R11 (100 $\Omega$), R16 (100 $\Omega$), and RLED (4.7 k$\Omega$, printed as 4K7).
- **Missing Potentiometers:** The SHIFT potentiometer (B25K) was entirely dropped from the output.
- **Missing Diodes/Optoelectronics:** The primary indicator LED (3mm Red LED) was skipped, likely because it lacked the standard `D\d+` reference designation used by the clipping diodes.
- **Ignored Component Substitutions:** The regex missed the alternative transistors (2N5089 for Q1/Q4; MPSA13 for Q2/Q3) and alternative diodes (1N4148 for D1/D2).
- **Uncaptured Build Modifications:** The "Cleft Mod" instruction to omit capacitor C6 was ignored—a classic symptom of regex engines lacking the structural awareness to handle edge-case semantic notes.

## Blender

- **Omitted Resistors:** The parser completely missed two 820Ω resistors, specifically R16 and R25.
- **Miscategorized Semiconductors:** Four 1N34A diodes (D1, D2, D3, and D4) were incorrectly grouped under the "Potentiometers" category rather than "Semiconductors" or "Diodes".
- **Incorrect Total Count:** The extracted metadata reports 59 parts found, whereas the bill of materials actually contains 61 discrete components.

## B-Side Fuzz

- **TRIM Potentiometer:** The parser failed to extract the B25K trimmer potentiometer, despite its explicit presence in the main components table and the schematic.
- **Status LED:** The status LED was completely omitted, though it is actively detailed in the schematic and wiring diagram.
- **Electromechanical & Hardware Components:** The parser missed all off-board hardware specifications, specifically the top-mounted jacks, the 9-pin 3PDT footswitch, and the 4S125B enclosure.

## Celestial Drive

- **LED Specification:** Extracted D1, D2, and D4 as generic "3mm" components, omitting the explicit "Red LED" designation from the BOM.
- **3PDT Footswitch:** Failed to extract the 9-pin bypass switch detailed in the wiring diagram.
- **Audio Jacks:** Omitted the two 1/4" Input and Output jacks depicted in the wiring diagram.
- **DC Power Receptacle:** Missed the power jack illustrated at the top of the wiring diagram.
- **Enclosure:** Failed to capture the "125B enclosure" explicitly called out in the drill template.

## Specifically within the scope of this plan

This document catalogs known weaknesses in the current regex-based BOM parser. These gaps are explicitly captured as `xfail` tests in `tests/test_parser_gaps.py`. The acceptance criteria for Milestone 2 (Linguistic Upgrade) is for these tests to pass naturally without breaking existing snapshot regressions.

1. **Truncated Switch States**
   - *Test:* `test_gap_truncated_switch_states`
   - *Issue:* The regex parser extracts the base component (e.g., `SPDT`) but greedily discards parenthetical state modifiers like `(On/Off/On)`.

1. **BS 1852 Notation Misses**
   - *Test:* `test_gap_bs1852_notation`
   - *Issue:* Component values using European inline multipliers (e.g., `4K7`, `1k5`) are frequently missed or mis-tokenized because the regex expects standard suffix notation.

1. **Heuristic Miscategorization of Switches**
   - *Test:* `test_gap_switch_miscategorization`
   - *Issue:* Switches named after standard potentiometer functions (e.g., `GAIN SPDT On - On`) trigger the fallback logic and are incorrectly binned as `Potentiometers`.

1. **Value Extraction/Anchoring Errors**
   - *Test:* `test_gap_value_extraction_anchoring`
   - *Issue:* The parser occasionally anchors to schematic voltages (e.g., `9v1`) instead of the actual part number (`1N4739A`) listed adjacent to it in the BOM.

1. **Dropped Dielectric Specifications**
   - *Test:* `test_gap_dropped_dielectrics`
   - *Issue:* Appended material requirements (e.g., `1uF MLCC`) are dropped by the regex, collapsing specific component footprints into generic buckets.
