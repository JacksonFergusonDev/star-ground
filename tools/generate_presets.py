"""Preset Generation Tool.

This script acts as an ETL (Extract, Transform, Load) pipeline for the application's
BOM presets. It reads the typed parts lists in `raw_boms/` (Tayda's kits) and
downloads the publisher documents listed in `raw_boms/<source>/sources.json`
(PedalPCB's build documents, which the repository doesn't keep), parses them using
the core library, and compiles them into a static Python dictionary
(`_presets_data.py`).

This allows the Streamlit app to load verified BOMs instantly without
needing to parse files on-the-fly during runtime.
"""

import json
import os
import tempfile
from pathlib import Path

import requests

from src.bom_lib import (
    ComponentCategory,
    ComponentKey,
    parse_pedalpcb_pdf,
    serialize_inventory,
)

INPUT_DIR = "raw_boms"
OUTPUT_FILE = "src/bom_lib/_presets_data.py"
SOURCES_FILE = "sources.json"
USER_AGENT = "star-ground-presets (+https://github.com/JacksonFergusonDev/star-ground)"


def _source_label(raw_source: str) -> str:
    """The preset browser's name for a source folder ('pedalpcb' -> 'PedalPCB')."""
    return "PedalPCB" if raw_source.lower() == "pedalpcb" else raw_source.capitalize()


def _pdf_builds(pdf_path: str, project_name: str, source: str) -> list[tuple[str, str]]:
    """Parses a build document into one preset per build.

    Multi-circuit documents (e.g. Muffin Fuzz's versions) become
    "Title: Build" presets.

    Args:
        pdf_path: The downloaded document.
        project_name: The name to use if the document has no title.
        source: The preset browser's source name.

    Returns:
        Each build's name and serialized parts list.
    """
    res = parse_pedalpcb_pdf(pdf_path, source_name=project_name)
    # Use extracted title from PDF metadata if available
    extracted = res.stats.get("extracted_title")
    if extracted:
        project_name = extracted.strip()
        print(f"      ↳ Found Title: {project_name}")

    inventories = [
        (f"{project_name}: {v.name}", v.inventory) for v in res.variants
    ] or [(project_name, res.inventory)]

    builds = []
    for build_name, inv in inventories:
        if not inv:
            continue
        # Special Handling: PedalPCB logic
        # Inject the PCB part BEFORE serialization so the "PCB" line
        # appears in the preset even if the BOM table omits it.
        if source == "PedalPCB":
            pcb_key = ComponentKey(ComponentCategory.PCB, f"{project_name} PCB")
            inv[pcb_key]["qty"] += 1
            inv[pcb_key]["refs"].append("PCB")

        # Use the shared library function to format the output string
        builds.append((build_name, serialize_inventory(inv)))
    return builds


def _download(session: requests.Session, url: str, dest: Path) -> None:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    dest.write_bytes(response.content)


def main() -> None:
    """Main execution entry point.

    Reads the typed parts lists in 'raw_boms', categorized by folder, then
    downloads and parses each publisher document its 'sources.json' files
    list, and serializes the results into a Python source file.
    """
    if not os.path.exists(INPUT_DIR):
        os.makedirs(INPUT_DIR)
        print(f"Created directory '{INPUT_DIR}'.")
        # Do not return here; the user might have created the root folder
        # and wants to proceed with nested manual folders.

    presets: dict[str, dict[str, str]] = {}

    print(f"🔍 Scanning {INPUT_DIR}...")

    def add(
        key_source: str, category: str, builds: list[tuple[str, str]], **meta: str
    ) -> None:
        for build_name, final_text in builds:
            if category:
                key = f"[{key_source}] [{category}] {build_name}"
            else:
                key = f"[{key_source}] {build_name}"
            presets[key] = {"bom_text": final_text, **meta}

    # 1. Typed parts lists (Tayda): raw_boms/<source>/<category>/<name>.txt
    for root, _dirs, files in os.walk(INPUT_DIR):
        rel_path = os.path.relpath(root, INPUT_DIR)
        path_parts = ["Misc"] if rel_path == "." else rel_path.split(os.sep)
        source = _source_label(path_parts[0])
        category = path_parts[1].capitalize() if len(path_parts) > 1 else ""
        for file in files:
            if not file.lower().endswith(".txt"):
                continue
            file_path = os.path.join(root, file)
            # Clean up the filename: "big_muff" -> "Big Muff"
            project_name = (
                os.path.splitext(file)[0].replace("_", " ").replace("-", " ").title()
            )
            with open(file_path, encoding="utf-8") as f:
                print(f"   📄 Read Text: {file}")
                add(
                    source,
                    category,
                    [(project_name, f.read())],
                    source_path=file_path.replace("\\", "/"),
                )

    # 2. Publisher documents: raw_boms/<source>/sources.json lists their URLs
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for sources_file in sorted(Path(INPUT_DIR).glob(f"*/{SOURCES_FILE}")):
        source = _source_label(sources_file.parent.name)
        documents = json.loads(sources_file.read_text(encoding="utf-8"))["documents"]
        for doc in documents:
            url = doc["url"]
            file_name = url.rsplit("/", 1)[-1]
            project_name = (
                os.path.splitext(file_name)[0]
                .replace("_", " ")
                .replace("-", " ")
                .title()
            )
            print(f"   ⚙️ Parsing PDF: {url}")
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    pdf_path = Path(tmp) / file_name
                    _download(session, url, pdf_path)
                    builds = _pdf_builds(str(pdf_path), project_name, source)
            except Exception as e:
                print(f"   ❌ Error parsing {url}: {e}")
                continue
            if not builds:
                print(f"   ⚠️ Skipping {url}: No parts found.")
                continue
            add(source, doc["category"].capitalize(), builds, source_url=url)

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
            for field in ("source_path", "source_url"):
                if field in data:
                    f.write(f'        "{field}": {json.dumps(data[field])},\n')
            f.write("    },\n")

        f.write("}\n")

    print("✅ Done!")


if __name__ == "__main__":
    main()
