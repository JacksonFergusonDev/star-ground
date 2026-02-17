"""
PDF Generation Engine.

This module handles the creation of printable assets for the build process:
1. Field Manuals: Step-by-step build checklists sorted by component height (Z-Height).
2. Sticker Sheets: Component organization labels formatted for Avery 5160 templates.

It uses the `fpdf2` library to generate PDFs in memory and bundles them into
ZIP archives for user download.
"""

import datetime
import io
import os
import re
import zipfile
from collections import defaultdict

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from src.bom_lib import Inventory, deduplicate_refs


def condense_refs(refs: list[str]) -> str:
    """
    Condenses a list of component references into a human-readable range string.

    Example:
        Input:  ['R1', 'R2', 'R3', 'C1', 'Q3', 'Q4']
        Output: 'C1, Q3-Q4, R1-R3'

    Args:
        refs (list[str]): A list of reference designators (e.g., "R1").

    Returns:
        str: A sorted, comma-separated string with consecutive ranges collapsed.
    """
    if not refs:
        return ""

    # 1. Parse into (Prefix, Number) tuples
    parsed = []
    pattern = re.compile(r"([a-zA-Z]+)(\d+)")

    unparseable = []

    for r in refs:
        m = pattern.match(r)
        if m:
            parsed.append((m.group(1), int(m.group(2))))
        else:
            unparseable.append(r)

    # 2. Sort primarily by Prefix (C, R, U), secondarily by Number (1, 2, 10)
    parsed.sort(key=lambda x: (x[0], x[1]))

    # 3. Group by Prefix
    groups = defaultdict(list)
    for p, n in parsed:
        groups[p].append(n)

    result_parts = sorted(unparseable)

    # 4. Range Finding Algorithm
    for prefix in sorted(groups.keys()):
        nums = groups[prefix]
        if not nums:
            continue

        ranges = []
        start = nums[0]
        prev = nums[0]

        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                # Range break detected
                if start == prev:
                    ranges.append(f"{prefix}{start}")
                else:
                    ranges.append(f"{prefix}{start}-{prefix}{prev}")
                start = n
                prev = n

        # Handle the final range
        if start == prev:
            ranges.append(f"{prefix}{start}")
        else:
            ranges.append(f"{prefix}{start}-{prefix}{prev}")

        result_parts.extend(ranges)

    return ", ".join(result_parts)


def clean_val_for_display(val: str) -> str:
    """Standardizes component value strings for cleaner PDF labels."""
    if "DIP SOCKET" in val.upper():
        return "DIP Socket"
    return val


class StickerSheet(FPDF):
    """
    FPDF Subclass for generating Avery 5160 component labels.

    Layout:
        - 3 Columns x 10 Rows (30 labels per page).
        - Dimensions: 2.625" x 1" (66.6mm x 25.4mm).
        - Margins: optimized for standard US Letter.
    """

    def __init__(self):
        # Letter size (215.9mm x 279.4mm)
        super().__init__(format="Letter", unit="mm")
        self.set_auto_page_break(auto=False)
        self.set_margins(4.8, 12.7, 4.8)  # Left 0.19", Top 0.5"

        # Avery 5160 Dims (Modified slightly for manual cutting tolerance)
        self.label_w = 66.6
        self.label_h = 25.4
        self.h_gap = 0.0  # No gap = shared borders for single-cut lines
        self.v_gap = 0.0  # No gap between rows

        self.cols = 3
        self.rows = 10
        self.current_idx = 0

        self.add_page()

    def add_sticker(self, project_code: str, part_val: str, refs: list[str], qty: int):
        """
        Draws a single sticker at the next available slot.

        Args:
            project_code (str): Short code for the project (e.g., "BIGM").
            part_val (str): Component value (e.g., "10k").
            refs (list[str]): List of component references.
            qty (int): Total quantity of this part.
        """
        # Calculate Position Index
        page_idx = self.current_idx % (self.cols * self.rows)
        if self.current_idx > 0 and page_idx == 0:
            self.add_page()

        col = page_idx % self.cols
        row = page_idx // self.cols

        x = 4.8 + (col * (self.label_w + self.h_gap))
        y = 12.7 + (row * (self.label_h + self.v_gap))

        self.set_xy(x, y)

        # Draw Cut Line (Border)
        self.set_line_width(0.1)
        self.set_draw_color(150, 150, 150)  # Light Grey cut lines
        self.rect(x, y, self.label_w, self.label_h)
        self.set_draw_color(0, 0, 0)  # Reset to black

        # Content Layer
        # 1. Top Left: Project Code
        self.set_font("Helvetica", "B", 8)
        self.cell(
            self.label_w,
            4,
            f"[{project_code}]",
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        # 2. Center: Component Value
        display_val = clean_val_for_display(part_val)
        self.set_xy(x, y + 4)
        self.set_font("Helvetica", "B", 12)
        # Truncate slightly to prevent overflow
        self.cell(
            self.label_w,
            8,
            str(display_val)[:18],
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        # 3. Bottom: Refs (Condensed)
        self.set_xy(x, y + 13)
        self.set_font("Helvetica", "", 7)
        ref_text = condense_refs(refs)

        # Add Quantity prefix if multiple items exist in the bag
        if qty > 1:
            ref_text = f"(x{qty}) {ref_text}"

        self.multi_cell(self.label_w, 3, ref_text, align="C")

        self.current_idx += 1


class FieldManual(FPDF):
    """
    FPDF Subclass for generating the 'Field Manual' build document.

    Features:
        - Automatic pagination.
        - Custom header/footer.
        - Logic to handle large checklists that span multiple pages.
    """

    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        self.set_title("Star Ground Field Manual")

    def header(self):
        """Renders the header on every page."""
        self.set_font("Courier", "B", 10)
        self.cell(
            0,
            10,
            "Star Ground Field Manual",
            align="R",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.line(10, 20, 200, 20)
        self.ln(10)

    def footer(self):
        """Renders the footer on every page."""
        self.set_y(-15)
        self.set_font("Courier", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def draw_checkbox(self, x: float, y: float):
        """Draws a square checkbox at the specified coordinates."""
        self.rect(x, y, 4, 4)

    def add_project(self, project_name: str, parts: list[dict]):
        """
        Adds a full project checklist to the PDF.

        Args:
            project_name (str): The name of the project.
            parts (list[dict]): Sorted list of component dictionaries.
        """
        self.add_page()

        # Project Title Block
        self.set_font("Courier", "B", 16)
        self.cell(
            0, 10, f"Project: {project_name}", new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )
        self.set_font("Courier", "", 10)
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        self.cell(0, 6, f"Date: {date_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Legend (Critical Info)
        self.set_font("Courier", "I", 8)
        self.set_text_color(220, 50, 50)  # Red
        self.cell(
            0,
            5,
            "Legend: Red Text = Polarized Component",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.set_text_color(0, 0, 0)  # Reset

        self.ln(2)

        # Table Headers
        self.set_font("Courier", "B", 10)
        self.cell(10, 8, "Chk", 1)
        self.cell(15, 8, "Qty", 1)
        self.cell(60, 8, "Value", 1)
        self.cell(
            0, 8, "Refs", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )  # Takes remaining width

        # Table Rows
        self.set_font("Courier", "", 9)

        for part in parts:
            # Prepare notes
            notes = part.get("notes", "")
            if part.get("polarized"):
                notes = f"[!] {notes}" if notes else "[!] Polarized"

            # Page Overflow Check:
            # Check if the current row (height 8) will cross the bottom margin.
            if self.get_y() + 8 > self.page_break_trigger:
                self.add_page()
                # Optional: Re-print headers here if desired.

            # Draw Checkbox manually
            x = self.get_x()
            y = self.get_y()
            self.draw_checkbox(x + 3, y + 2)
            self.cell(10, 8, "", 1)  # [Chk] column is empty (just the box)

            # [Qty]
            self.cell(15, 8, str(part["qty"]), 1, align="C")

            # [Value] & Notes logic
            raw_val = str(part["value"])
            val_str = clean_val_for_display(raw_val)

            # Highlight logic: Red text for warnings/polarity
            if part.get("polarized") or part.get("notes"):
                self.set_text_color(220, 50, 50)  # Red
                if notes:
                    clean_note = notes.replace("[!] ", "")
                    # Don't append note if it's just repeating "DIP Socket"
                    if "DIP Socket" not in val_str:
                        val_str = f"{val_str} [{clean_note}]"
            else:
                self.set_text_color(0, 0, 0)  # Black

            # Draw Value Cell (Expanded Width)
            self.cell(60, 8, val_str[:35], 1)

            # Reset color to black
            self.set_text_color(0, 0, 0)

            # [Refs]
            refs = ", ".join(part["refs"])
            if len(refs) > 50:
                refs = refs[:47] + "..."  # Truncate if extremely long
            self.cell(0, 8, refs, 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def sort_by_z_height(part_list: list[dict]) -> list[dict]:
    """
    Sorts components by their physical Z-Height (Low to High).

    This ordering dictates the most efficient soldering sequence:
    1. PCB (Base)
    2. Resistors / Diodes (Flush to board)
    3. Sockets / ICs
    4. Small Capacitors
    5. Transistors
    6. Electrolytics (Tall)
    7. Potentiometers / Mechanicals (Tallest/Rigid)

    Args:
        part_list (list[dict]): List of component parts.

    Returns:
        list[dict]: The sorted list.
    """
    # Mapping Categories to Rank (Lower number = Earlier in build)
    z_map = {
        "PCB": 0,  # First
        "Resistors": 10,
        "Diodes": 15,
        # Sockets will be injected at 18
        "Crystals/Oscillators": 30,
        "Capacitors": 40,  # Default (Small)
        "Transistors": 50,
        # Electros will be injected at 60
        "Switches": 70,
        "Potentiometers": 80,  # "Second Last" (Mechanicals)
        "Hardware/Misc": 85,  # Jacks, etc.
        "ICs": 90,  # "Last" (Chip Insertion)
    }

    def get_rank(item):
        cat = item["category"]
        val = str(item["value"])

        # 1. Socket Check (Priority Override)
        # Sockets are usually in "Hardware/Misc" or "ICs" but need to be soldered early
        if "SOCKET" in val.upper():
            return 18

        # 2. Capacitor Check (Electro vs Ceramic)
        if cat == "Capacitors":
            # Electros are tall -> Late build
            if ("u" in val or "µ" in val) and float_val_check(val) >= 1.0:
                return 60  # Electrolytics rank
            return 40  # Small caps

        return z_map.get(cat, 99)

    return sorted(part_list, key=get_rank)


def float_val_check(val_str: str) -> float:
    """
    Heuristic to detect bulk capacitance (Electrolytics).

    Args:
        val_str (str): The component value (e.g., "100uF").

    Returns:
        float: 1.0 if likely electrolytic, 0.0 otherwise.
    """
    if not val_str:
        return 0.0

    if "u" in val_str or "µ" in val_str:
        return 1.0

    return 0.0


def _write_field_manuals(zf: zipfile.ZipFile, inventory: dict, slots: list[dict]):
    """Helper: Generates Field Manual PDFs and writes them to the ZIP archive."""
    processed_projects = set()

    for slot in slots:
        project_name = slot.get("locked_name", slot["name"])
        if not project_name:
            continue

        # Prevent duplicates if multiple slots have the same project name
        if project_name in processed_projects:
            continue
        processed_projects.add(project_name)

        pdf = FieldManual()
        project_parts = []

        # Filter Global Inventory for this specific Project
        for key, data in inventory.items():
            sources = data["sources"]
            if project_name in sources:
                unique_refs = deduplicate_refs(sources[project_name])
                if unique_refs:
                    cat, val = key.split(" | ", 1)

                    # Annotations Logic
                    row_notes = ""
                    if "DIP SOCKET" in val:
                        row_notes = "[!] Check Size"
                    is_polarized = cat in ["Diodes", "Transistors", "ICs"] or (
                        cat == "Capacitors" and ("u" in val or "µ" in val)
                    )

                    project_parts.append(
                        {
                            "category": cat,
                            "value": val,
                            "qty": len(unique_refs),
                            "refs": unique_refs,
                            "notes": row_notes,
                            "polarized": is_polarized,
                        }
                    )

        if project_parts:
            # Sort parts by Z-Height for the manual
            sorted_parts = sort_by_z_height(project_parts)
            pdf.add_project(project_name, sorted_parts)

            safe_name = re.sub(r'[<>:"/\\|?*]', "", project_name).strip()
            zf.writestr(
                f"Field Manuals/{safe_name} Field Manual.pdf", bytes(pdf.output())
            )


def _write_stickers(zf: zipfile.ZipFile, inventory: dict, slots: list[dict]):
    """Helper: Generates Sticker Sheet PDFs and writes them to the ZIP archive."""
    processed_projects = set()

    for slot in slots:
        project_name = slot.get("locked_name", slot["name"])
        if not project_name:
            continue

        # Prevent duplicates
        if project_name in processed_projects:
            continue
        processed_projects.add(project_name)

        project_parts = []
        for key, data in inventory.items():
            sources = data["sources"]
            if project_name in sources:
                unique_refs = deduplicate_refs(sources[project_name])
                if unique_refs:
                    _, val = key.split(" | ", 1)
                    project_parts.append((val, unique_refs))

        if not project_parts:
            continue

        pdf = StickerSheet()
        # Generate a 4-char Short Code for the label (e.g. "Big Muff" -> "BIGM")
        code = "".join([c for c in project_name if c.isalnum()]).upper()[:4]
        project_parts.sort(key=lambda x: x[0])

        for val, refs in project_parts:
            pdf.add_sticker(code, val, refs, len(refs))

        safe_name = re.sub(r'[<>:"/\\|?*]', "", project_name).strip()
        zf.writestr(
            f"Sticker Sheets/{safe_name} Sticker Sheet.pdf", bytes(pdf.output())
        )


def generate_pdf_bundle(inventory: Inventory, slots: list[dict]) -> bytes:
    """
    Generates a ZIP file containing only the generated PDFs (Manuals + Stickers).

    Args:
        inventory (Inventory): The master inventory object.
        slots (list[dict]): The list of project slots.

    Returns:
        bytes: The binary content of the ZIP file.
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_field_manuals(zf, inventory.data, slots)
        _write_stickers(zf, inventory.data, slots)
    return zip_buffer.getvalue()


def generate_master_zip(
    inventory: Inventory, slots: list[dict], shopping_list_csv: bytes, stock_csv: bytes
) -> bytes:
    """
    Generates the "Master ZIP" containing all project artifacts.

    Contents:
    1. CSVs (Shopping List, Inventory Update)
    2. Generated PDFs (Field Manuals, Sticker Sheets)
    3. Source Documents (Original PDFs/Txt files)
    4. Info.txt (Metadata)

    Args:
        inventory (Inventory): The master inventory.
        slots (list[dict]): The project slots.
        shopping_list_csv (bytes): The CSV bytes for the shopping list.
        stock_csv (bytes): The CSV bytes for the stock update.

    Returns:
        bytes: The binary content of the Master ZIP.
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Root Files
        zf.writestr("Shopping List.csv", shopping_list_csv)
        zf.writestr("My Inventory Updated.csv", stock_csv)

        info_text = (
            "Star Ground v2.1.2\n"
            "By: Jackson Ferguson\n"
            "Generated on: "
            + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            + "\n\n"
            "CONTENTS:\n"
            "- Field Manuals/: Printable step-by-step checklists.\n"
            "- Sticker Sheets/: Labels for Avery 5160 (3x10).\n"
            "- Source Documents/: The original build docs (if available).\n\n"
            "Github Page:\n"
            "https://github.com/JacksonFergusonDev/star-ground\n"
        )
        zf.writestr("info.txt", info_text)

        # 2. Generated PDFs
        _write_field_manuals(zf, inventory.data, slots)
        _write_stickers(zf, inventory.data, slots)

        # 3. Source Documents (Preservation Logic)
        used_filenames = set()

        for slot in slots:
            project_name = slot.get("locked_name", slot["name"])
            if not project_name:
                continue

            safe_name = re.sub(r'[<>:"/\\|?*]', "", project_name).strip()

            file_content = None
            dest_name = ""

            # Strategy A: Use Cached Bytes (URL/Upload)
            if "cached_pdf_bytes" in slot and slot["cached_pdf_bytes"]:
                file_content = slot["cached_pdf_bytes"]
                dest_name = f"Source Documents/{safe_name} Source.pdf"

            # Strategy B: Use Local Path (Preset)
            elif "source_path" in slot and slot["source_path"]:
                try:
                    src_path = slot["source_path"]
                    _, ext = os.path.splitext(src_path)
                    if not ext:
                        ext = ".txt"

                    with open(src_path, "rb") as f:
                        file_content = f.read()
                    dest_name = f"Source Documents/{safe_name} Source{ext}"
                except Exception:
                    pass

            # Strategy C: Legacy Fallback (Session State Backwards Compatibility)
            elif "pdf_path" in slot and slot["pdf_path"]:
                try:
                    with open(slot["pdf_path"], "rb") as f:
                        file_content = f.read()
                    dest_name = f"Source Documents/{safe_name} Source.pdf"
                except Exception:
                    pass

            # Deduplication Logic:
            # If we've already added a file with this name (e.g. "Big Muff Source.pdf"),
            # we simply skip adding it again to avoid redundancy/warnings.
            if file_content and dest_name and dest_name not in used_filenames:
                zf.writestr(dest_name, file_content)
                used_filenames.add(dest_name)

    return zip_buffer.getvalue()
