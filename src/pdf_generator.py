from fpdf import FPDF
from fpdf.enums import XPos, YPos
from collections import defaultdict
import datetime
import zipfile
import io
import re
from src.bom_lib import deduplicate_refs


def condense_refs(refs):
    """
    Condenses a list of refs: ['R1', 'R2', 'R3', 'C1', 'Q3', 'Q4'] -> 'C1, Q3-Q4, R1-R3'
    """
    if not refs:
        return ""

    # 1. Parse into (Prefix, Number)
    parsed = []
    pattern = re.compile(r"([a-zA-Z]+)(\d+)")

    unparseable = []

    for r in refs:
        m = pattern.match(r)
        if m:
            parsed.append((m.group(1), int(m.group(2))))
        else:
            unparseable.append(r)

    # 2. Sort by Prefix then Number
    parsed.sort(key=lambda x: (x[0], x[1]))

    # 3. Group and condense
    groups = defaultdict(list)
    for p, n in parsed:
        groups[p].append(n)

    result_parts = sorted(unparseable)

    for prefix in sorted(groups.keys()):
        nums = groups[prefix]
        if not nums:
            continue

        # Range finding algorithm
        ranges = []
        start = nums[0]
        prev = nums[0]

        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                # Range ended
                if start == prev:
                    ranges.append(f"{prefix}{start}")
                else:
                    ranges.append(f"{prefix}{start}-{prefix}{prev}")
                start = n
                prev = n

        # Final range
        if start == prev:
            ranges.append(f"{prefix}{start}")
        else:
            ranges.append(f"{prefix}{start}-{prefix}{prev}")

        result_parts.extend(ranges)

    return ", ".join(result_parts)


class StickerSheet(FPDF):
    def __init__(self):
        # Letter size (215.9mm x 279.4mm)
        super().__init__(format="Letter", unit="mm")
        self.set_auto_page_break(auto=False)
        self.set_margins(4.8, 12.7, 4.8)  # Left 0.19", Top 0.5"

        # Avery 5160 Dims (Modified for Manual Cutting)
        self.label_w = 66.6  # 2.625"
        self.label_h = 25.4  # 1.0"
        self.h_gap = 0.0  # No gap = shared borders for single-cut lines
        self.v_gap = 0.0  # 0.0" between rows

        self.cols = 3
        self.rows = 10
        self.current_idx = 0

        self.add_page()

    def add_sticker(self, project_code, part_val, refs, qty):
        # Calculate Position
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

        # Content
        # Top Left: Project Code
        self.set_font("Helvetica", "B", 8)
        self.cell(
            self.label_w,
            4,
            f"[{project_code}]",
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        # Center: Value
        self.set_xy(x, y + 4)
        self.set_font("Helvetica", "B", 12)
        self.cell(
            self.label_w,
            8,
            str(part_val)[:18],
            align="C",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

        # Bottom: Refs (Condensed/Truncated)
        self.set_xy(x, y + 13)
        self.set_font("Helvetica", "", 7)
        ref_text = condense_refs(refs)

        # Add Quantity if > 1
        if qty > 1:
            ref_text = f"(x{qty}) {ref_text}"

        self.multi_cell(self.label_w, 3, ref_text, align="C")

        self.current_idx += 1


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


def _write_field_manuals(zf, inventory, slots):
    """Helper: Generate Field Manual PDFs and write to ZIP."""
    for slot in slots:
        project_name = slot.get("locked_name", slot["name"])
        if not project_name:
            continue

        pdf = FieldManual()
        project_parts = []

        # Filter Inventory
        for key, data in inventory.items():
            sources = data["sources"]
            if project_name in sources:
                unique_refs = deduplicate_refs(sources[project_name])
                if unique_refs:
                    cat, val = key.split(" | ", 1)

                    # Formatting logic
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
            sorted_parts = sort_by_z_height(project_parts)
            pdf.add_project(project_name, sorted_parts)

            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name)
            zf.writestr(
                f"Field Manuals/{safe_name}_Field_Manual.pdf", bytes(pdf.output())
            )


def _write_stickers(zf, inventory, slots):
    """Helper: Generate Sticker Sheet PDFs and write to ZIP."""
    for slot in slots:
        project_name = slot.get("locked_name", slot["name"])
        if not project_name:
            continue

        project_parts = []
        for key, data in inventory.items():
            sources = data["sources"]
            if project_name in sources:
                unique_refs = deduplicate_refs(sources[project_name])
                if unique_refs:
                    cat, val = key.split(" | ", 1)
                    project_parts.append((val, unique_refs))

        if not project_parts:
            continue

        pdf = StickerSheet()
        code = "".join([c for c in project_name if c.isalnum()]).upper()[:4]
        project_parts.sort(key=lambda x: x[0])

        for val, refs in project_parts:
            pdf.add_sticker(code, val, refs, len(refs))

        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name)
        zf.writestr(
            f"Sticker Sheets/{safe_name}_Sticker_Sheet.pdf", bytes(pdf.output())
        )


def generate_pdf_bundle(inventory, slots):
    """Option 1: Returns ZIP with Field Manuals and Sticker Sheets."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        _write_field_manuals(zf, inventory, slots)
        _write_stickers(zf, inventory, slots)
    return zip_buffer.getvalue()


def generate_master_zip(inventory, slots, shopping_list_csv, stock_csv):
    """Option 2: Returns ZIP with Everything (PDFs + CSVs + Source Files)."""
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Root Files
        zf.writestr("Pedal Shopping List.csv", shopping_list_csv)
        zf.writestr("My Inventory Updated.csv", stock_csv)

        info_text = (
            "Guitar Pedal BOM Manager\n"
            "Generated on: "
            + datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            + "\n\n"
            "CONTENTS:\n"
            "- Field Manuals/: Printable step-by-step checklists.\n"
            "- Sticker Sheets/: Labels for Avery 5160 (3x10).\n"
            "- Source Documents/: The original build docs (if available).\n"
        )
        zf.writestr("info.txt", info_text)

        # 2. Generated PDFs
        _write_field_manuals(zf, inventory, slots)
        _write_stickers(zf, inventory, slots)

        # 3. Source Documents
        for slot in slots:
            project_name = slot.get("locked_name", slot["name"])
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name)

            # Check cached bytes (URL/Upload)
            if "cached_pdf_bytes" in slot and slot["cached_pdf_bytes"]:
                zf.writestr(
                    f"Source Documents/{safe_name}_Source.pdf", slot["cached_pdf_bytes"]
                )

            # Check local path (Preset)
            elif "pdf_path" in slot and slot["pdf_path"]:
                try:
                    with open(slot["pdf_path"], "rb") as f:
                        zf.writestr(
                            f"Source Documents/{safe_name}_Source.pdf", f.read()
                        )
                except Exception:
                    pass

    return zip_buffer.getvalue()
