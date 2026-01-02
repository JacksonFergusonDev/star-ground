# Preset Generator Tool

This tool automates the creation of `src/presets.py` by scraping a folder of local BOM files.

## Usage

1. Create a folder named `raw_boms` in the project root.
2. Place your `.pdf` (PedalPCB) or `.csv` BOM files into that folder.
3. Run the script:
   ```bash
   python tools/generate_presets.py

4. The script will parse all files, flatten them into standard text format, and overwrite `src/presets.py`.

## Supported Formats

* **PedalPCB PDFs:** Uses `pdfplumber` to extract BOM tables.
* **CSV:** Expects columns `Ref` and `Value`.