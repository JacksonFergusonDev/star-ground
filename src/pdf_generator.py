from fpdf import FPDF
from fpdf.enums import XPos, YPos
import datetime
import zipfile
import io
import re
from src.bom_lib import deduplicate_refs


class FieldManual(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        self.set_title("Pedal BOM Field Manual")

    def header(self):
        self.set_font("Courier", "B", 10)
        self.cell(
            0,
            10,
            "Pedal Builder's Field Manual",
            align="R",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.line(10, 20, 200, 20)
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("Courier", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def draw_checkbox(self, x, y):
        self.rect(x, y, 4, 4)

    def add_project(self, project_name, parts):
        self.add_page()

        # Project Title
        self.set_font("Courier", "B", 16)
        self.cell(
            0, 10, f"Project: {project_name}", new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )
        self.set_font("Courier", "", 10)
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        self.cell(0, 6, f"Date: {date_str}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Legend
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

        self.ln(2)  # Reduced spacing since legend takes up some room

        # Headers
        self.set_font("Courier", "B", 10)
        self.cell(10, 8, "Chk", 1)
        self.cell(15, 8, "Qty", 1)
        self.cell(60, 8, "Value", 1)
        self.cell(
            0, 8, "Refs", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )  # Takes remaining space

        # Rows
        self.set_font("Courier", "", 9)

        for part in parts:
            # Calculate height for multiline notes
            notes = part["notes"]
            # Highlight Polarized/Critical info
            if part["polarized"]:
                notes = f"[!] {notes}" if notes else "[!] Polarized"

            # Check for page overflow BEFORE drawing the checkbox.
            # If we don't, the checkbox draws on the old page, and self.cell
            # pushes the text to the new page.
            # 8 is the cell height.
            if self.get_y() + 8 > self.page_break_trigger:
                self.add_page()
                # Note: We could reprint table headers here if desired,
                # but currently we just continue the list.

            # Draw Checkbox manually
            x = self.get_x()
            y = self.get_y()
            self.draw_checkbox(x + 3, y + 2)
            self.cell(10, 8, "", 1)  # [Chk]

            # [Qty]
            self.cell(15, 8, str(part["qty"]), 1, align="C")

            # Prepare Value & Notes
            val_str = str(part["value"])

            # Logic: Red text for warnings/polarity
            if part["polarized"] or part["notes"]:
                self.set_text_color(220, 50, 50)  # Red
                if part["notes"]:
                    clean_note = part["notes"].replace("[!] ", "")
                    val_str = f"{val_str} [{clean_note}]"
            else:
                self.set_text_color(0, 0, 0)  # Black

            # [Value] - Expanded Width (60mm)
            self.cell(60, 8, val_str[:35], 1)

            # Reset color
            self.set_text_color(0, 0, 0)

            # [Refs] - Auto Width (Remaining)
            refs = ", ".join(part["refs"])
            if len(refs) > 60:
                refs = refs[:57] + "..."
            self.cell(0, 8, refs, 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def sort_by_z_height(part_list):
    """
    Sorts parts for populating a board: Low to High.
    1. PCB
    2. Resistors / Diodes / Sockets
    3. Small Caps / Crystals
    4. Transistors
    5. Electrolytics
    6. Pots / Mechanicals
    7. ICs (Chips) - Always last
    """
    # Lower number = Earlier in build
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
            if "u" in val or "µ" in val:
                if float_val_check(val) >= 1.0:
                    return 60  # Electrolytics rank
            return 40  # Small caps

        return z_map.get(cat, 99)

    return sorted(part_list, key=get_rank)


def float_val_check(val_str: str) -> float:
    """
    Simple helper to guess if cap is big (electrolytic).
    Returns 1.0 if 'u' or 'µ' is present, else 0.0.
    """
    if not val_str:
        return 0.0

    if "u" in val_str or "µ" in val_str:
        return 1.0

    return 0.0


def generate_field_manual_zip(inventory, slots):
    """
    Generates a ZIP file containing individual Field Manual PDFs for each pedal.
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for slot in slots:
            project_name = slot["name"]
            if not project_name:
                continue

            # Create a FRESH PDF for this project
            pdf = FieldManual()

            project_parts = []

            # Filter Inventory for this project
            for key, data in inventory.items():
                sources = data["sources"]

                # Check if this project uses this part
                if project_name in sources:
                    # Deduplicate refs to get Single-Unit count
                    unique_refs = deduplicate_refs(sources[project_name])
                    qty = len(unique_refs)

                    if qty > 0:
                        cat, val = key.split(" | ", 1)

                        # Formatting Overrides
                        row_notes = ""
                        if "DIP SOCKET (Check Size)" in val:
                            val = "DIP SOCKET"
                            row_notes = "[!] Check Size"

                        is_polarized = cat in ["Diodes", "Transistors", "ICs"]
                        if cat == "Capacitors" and ("u" in val or "µ" in val):
                            is_polarized = True

                        project_parts.append(
                            {
                                "category": cat,
                                "value": val,
                                "qty": qty,
                                "refs": unique_refs,
                                "notes": row_notes,
                                "polarized": is_polarized,
                            }
                        )

            # Sort
            sorted_parts = sort_by_z_height(project_parts)

            # Add content (starts Page 1)
            pdf.add_project(project_name, sorted_parts)

            # Generate PDF bytes
            pdf_bytes = bytes(pdf.output())

            # Create Safe Filename
            # e.g. "Big Muff - Ram's Head" -> "Big_Muff_-_Rams_Head_Field_Manual.pdf"
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name)
            filename = f"{safe_name}_Field_Manual.pdf"

            # Write to ZIP
            zf.writestr(filename, pdf_bytes)

    return zip_buffer.getvalue()
