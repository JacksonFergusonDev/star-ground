import os
import sys

# Add the parent directory to sys.path to access src.bom_lib
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bom_lib import parse_pedalpcb_pdf, sort_inventory

INPUT_DIR = "raw_boms"
OUTPUT_FILE = "src/presets.py"


def serialize_inventory(inventory):
    """
    Converts the parsed dictionary back into the string format app.py expects.
    e.g. {'Resistors | 10k': refs=['R1', 'R2']} -> "R1 10k\nR2 10k"
    """
    lines = []

    # helper to clean the value (remove " | " prefix)
    def get_val(key):
        if " | " in key:
            return key.split(" | ", 1)[1]
        return key

    # Sort so the output text is tidy
    sorted_items = sort_inventory(inventory)

    for key, data in sorted_items:
        clean_val = get_val(key)

        # If we have specific refs (R1, C1), list them individually
        if data["refs"]:
            for ref in data["refs"]:
                # Ignore generic hardware refs if they slipped in
                if ref != "HW":
                    lines.append(f"{ref} {clean_val}")
        else:
            # Fallback for things without refs (rare in presets)
            lines.append(f"{clean_val} (Qty: {data['qty']})")

    return "\n".join(lines)


def generate_presets():
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        print(f"Created directory '{INPUT_DIR}'.")
        # Don't return, user might have nested folders

    presets = {}

    print(f"🔍 Scanning {INPUT_DIR}...")

    for root, dirs, files in os.walk(INPUT_DIR):
        for file in files:
            file_path = os.path.join(root, file)
            filename_no_ext = os.path.splitext(file)[0]

            # 1. Determine Metadata from Folder Structure
            # rel_path: "pedalpcb/fuzz"
            rel_path = os.path.relpath(root, INPUT_DIR)
            path_parts = rel_path.split(os.sep)

            # Handle root files safely
            if rel_path == ".":
                path_parts = ["Misc"]

            # Construct a clean Key: "[Source] [Category] Name"
            raw_source = path_parts[0] if path_parts else "Unsorted"
            # Special case for branding
            if raw_source.lower() == "pedalpcb":
                source = "PedalPCB"
            else:
                source = raw_source.capitalize()

            category = path_parts[1].capitalize() if len(path_parts) > 1 else ""

            # 2. Process File & Determine Name
            final_text = ""
            # Clean up the filename: "big_muff" -> "Big Muff"
            project_name = filename_no_ext.replace("_", " ").replace("-", " ").title()

            if file.lower().endswith(".txt"):
                # CASE A: Tayda / Raw Text
                # Trust the user's formatting (app.py verifies it anyway)
                with open(file_path, "r", encoding="utf-8") as f:
                    final_text = f.read()
                    print(f"   📄 Read Text: {file}")

            elif file.lower().endswith(".pdf"):
                # CASE B: PedalPCB PDF
                # Parse it, then serialize it back to text
                print(f"   ⚙️ Parsing PDF: {file}")
                try:
                    # Pass a temporary source name, we will refine the key later
                    inv, stats = parse_pedalpcb_pdf(file_path, source_name=project_name)

                    if stats["parts_found"] > 0:
                        final_text = serialize_inventory(inv)

                        # Use extracted title if available
                        extracted = stats.get("extracted_title")
                        if extracted:
                            project_name = extracted.strip()
                            print(f"      ↳ Found Title: {project_name}")

                        # If this is a PedalPCB project, inject the PCB part into the inventory
                        # BEFORE serialization so it becomes part of the text preset.
                        if source == "PedalPCB":
                            pcb_val = f"{project_name} PCB"
                            # We manually inject the dictionary entry expected by serialize_inventory
                            key = f"PCB | {pcb_val}"
                            inv[key]["qty"] += 1
                            inv[key]["refs"].append("PCB")

                        final_text = serialize_inventory(inv)
                    else:
                        print(f"   ⚠️ Skipping {file}: No parts found.")
                        continue
                except Exception as e:
                    print(f"   ❌ Error parsing {file}: {e}")
                    continue

            # 3. Build Final Key and Add to Dict
            if final_text:
                if category:
                    key = f"[{source}] [{category}] {project_name}"
                else:
                    key = f"[{source}] {project_name}"

                presets[key] = final_text

    # 4. Write Output
    print(f"💾 Writing {len(presets)} presets to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by tools/generate_presets.py\n")
        f.write("# DO NOT EDIT MANUALLY\n\n")
        f.write("BOM_PRESETS = {\n")

        # Sort keys for stability
        for k in sorted(presets.keys()):
            # Use triple quotes for readable multi-line strings
            # Indent the content by 8 spaces to match the dict structure
            content = presets[k].strip().replace("\n", "\n        ")
            f.write(f'    {repr(k)}: """\n        {content}\n    """,\n')

        f.write("}\n")

    print("✅ Done!")


if __name__ == "__main__":
    generate_presets()
