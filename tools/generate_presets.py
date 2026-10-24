"""Preset Generation Tool.

This script acts as an ETL (Extract, Transform, Load) pipeline for the application's
BOM presets. It crawls a specified directory of raw BOM files (PDFs and Text files),
parses them using the core library, and compiles them into a static Python dictionary
(`_presets_data.py`).

This allows the Streamlit app to load verified BOMs instantly without
needing to parse files on-the-fly during runtime.
"""

import json
import os

from src.bom_lib import (
    ComponentCategory,
    ComponentKey,
    parse_pedalpcb_pdf,
    serialize_inventory,
)

INPUT_DIR = "raw_boms"
OUTPUT_FILE = "src/bom_lib/_presets_data.py"


def main() -> None:
    """Main execution entry point.

    Walks the 'raw_boms' directory, categorizes files based on folder structure,
    parses content (handling PDFs via OCR/Scraping and Text via direct read),
    and serializes the results into a Python source file.
    """
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        print(f"Created directory '{INPUT_DIR}'.")
        # Do not return here; the user might have created the root folder
        # and wants to proceed with nested manual folders.

    presets = {}

    print(f"🔍 Scanning {INPUT_DIR}...")

    for root, _dirs, files in os.walk(INPUT_DIR):
        for file in files:
            file_path = os.path.join(root, file)
            filename_no_ext = os.path.splitext(file)[0]

            # 1. Determine Metadata from Folder Structure
            # Example rel_path: "pedalpcb/fuzz" -> Source: PedalPCB, Category: Fuzz
            rel_path = os.path.relpath(root, INPUT_DIR)
            path_parts = rel_path.split(os.sep)

            # Handle files in the root directory safely
            if rel_path == ".":
                path_parts = ["Misc"]

            # Construct a clean Key: "[Source] [Category] Name"
            raw_source = path_parts[0] if path_parts else "Unsorted"

            # Special case for branding consistency
            if raw_source.lower() == "pedalpcb":
                source = "PedalPCB"
            else:
                source = raw_source.capitalize()

            category = path_parts[1].capitalize() if len(path_parts) > 1 else ""

            # 2. Process File & Determine Name
            # A file yields one preset per build: multi-circuit PDFs (e.g. Muffin
            # Fuzz's versions) become "Title: Build" presets.
            builds: list[tuple[str, str]] = []
            # Clean up the filename: "big_muff" -> "Big Muff"
            project_name = filename_no_ext.replace("_", " ").replace("-", " ").title()

            if file.lower().endswith(".txt"):
                # CASE A: Tayda / Raw Text
                # We trust the user's formatting here (app.py verification handles validaty later)
                with open(file_path, encoding="utf-8") as f:
                    builds.append((project_name, f.read()))
                    print(f"   📄 Read Text: {file}")

            elif file.lower().endswith(".pdf"):
                # CASE B: PedalPCB PDF
                # We parse the PDF into an inventory, then serialize it back to standardized text.
                print(f"   ⚙️ Parsing PDF: {file}")
                try:
                    res = parse_pedalpcb_pdf(file_path, source_name=project_name)
                    # Use extracted title from PDF metadata if available
                    extracted = res.stats.get("extracted_title")
                    if extracted:
                        project_name = extracted.strip()
                        print(f"      ↳ Found Title: {project_name}")

                    inventories = [
                        (f"{project_name}: {v.name}", v.inventory) for v in res.variants
                    ] or [(project_name, res.inventory)]

                    for build_name, inv in inventories:
                        if not inv:
                            continue
                        # Special Handling: PedalPCB logic
                        # Inject the PCB part BEFORE serialization so the "PCB" line
                        # appears in the preset even if the BOM table omits it.
                        if source == "PedalPCB":
                            pcb_key = ComponentKey(
                                ComponentCategory.PCB, f"{project_name} PCB"
                            )
                            inv[pcb_key]["qty"] += 1
                            inv[pcb_key]["refs"].append("PCB")

                        # Use the shared library function to format the output string
                        builds.append((build_name, serialize_inventory(inv)))
                    if not builds:
                        print(f"   ⚠️ Skipping {file}: No parts found.")
                        continue
                except Exception as e:
                    print(f"   ❌ Error parsing {file}: {e}")
                    continue

            # 3. Build Final Key and Add to Dict
            for build_name, final_text in builds:
                if category:
                    key = f"[{source}] [{category}] {build_name}"
                else:
                    key = f"[{source}] {build_name}"

                # Store metadata structure
                presets[key] = {
                    "bom_text": final_text,
                    "source_path": file_path.replace("\\", "/"),
                    "is_pdf": file.lower().endswith(".pdf"),
                }

    # 4. Write Output
    print(f"💾 Writing {len(presets)} presets to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by tools/generate_presets.py\n")
        f.write("# DO NOT EDIT MANUALLY\n\n")
        f.write("BOM_PRESETS = {\n")

        # Sort keys for deterministic output
        for k in sorted(presets.keys()):
            data = presets[k]
            # Manual formatting to ensure BOM text uses Python triple quotes correctly.
            # We indent deeply (12 spaces) to align inside the dict structure.
            content = str(data["bom_text"]).strip().replace("\n", "\n            ")

            f.write(f"    {json.dumps(k)}: {{\n")
            f.write(f'        "bom_text": """\n            {content}\n        """,\n')
            f.write(f'        "source_path": {json.dumps(data["source_path"])},\n')
            f.write(f'        "is_pdf": {data["is_pdf"]},\n')
            f.write("    },\n")

        f.write("}\n")

    print("✅ Done!")


if __name__ == "__main__":
    main()
