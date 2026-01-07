from fpdf import FPDF
import datetime


class FieldManual(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=15)
        self.set_title("Pedal BOM Field Manual")

    def header(self):
        self.set_font("Courier", "B", 10)
        self.cell(0, 10, "Pedal Builder's Field Manual", ln=True, align="R")
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
        self.cell(0, 10, f"Project: {project_name}", ln=True)
        self.set_font("Courier", "", 10)
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        self.cell(0, 6, f"Date: {date_str}", ln=True)
        self.ln(5)

        # Headers
        self.set_font("Courier", "B", 10)
        self.cell(10, 8, "Chk", 1)
        self.cell(15, 8, "Qty", 1)
        self.cell(35, 8, "Value", 1)
        self.cell(50, 8, "Refs", 1)
        self.cell(0, 8, "Notes", 1, ln=True)

        # Rows
        self.set_font("Courier", "", 9)

        for part in parts:
            # Calculate height for multiline notes
            notes = part["notes"]
            # Highlight Polarized/Critical info
            if part["polarized"]:
                notes = f"[!] {notes}" if notes else "[!] Polarized"

            # Simple text wrapping for notes could be complex,
            # for now we truncate or let fpdf handle simple overflow if using multi_cell,
            # but tables in FPDF are tricky. We'll use fixed cells for simplicity.

            # Draw Checkbox manually to look nice
            x = self.get_x()
            y = self.get_y()
            self.draw_checkbox(x + 3, y + 2)
            self.cell(10, 8, "", 1)  # Empty cell for box

            self.cell(15, 8, str(part["qty"]), 1, align="C")

            # Value
            val_str = str(part["value"])[:18]  # Truncate if too long
            self.cell(35, 8, val_str, 1)

            # Refs (Smart Truncation)
            refs = ", ".join(part["refs"])
            if len(refs) > 25:
                refs = refs[:22] + "..."
            self.cell(50, 8, refs, 1)

            # Notes
            self.cell(0, 8, notes[:45], 1, ln=True)


def sort_by_z_height(part_list):
    """
    Sorts parts for populating a board: Low to High.
    1. Resistors / Diodes
    2. Sockets (ICs)
    3. Small Caps (Ceramic/Film)
    4. Transistors
    5. Electrolytics
    6. Wire/Hardware
    """
    # Lower number = Earlier in build
    z_map = {
        "Resistors": 10,
        "Diodes": 15,
        "ICs": 20,  # Sockets go in early
        "Capacitors": 30,  # Default Cap
        "Transistors": 40,
        "Crystals/Oscillators": 45,
        "Potentiometers": 80,
        "Switches": 85,
        "Hardware/Misc": 90,
    }

    def get_rank(item):
        cat = item["category"]
        val = item["value"]

        # Specific overrides
        if cat == "Capacitors":
            # Electros are tall -> Late build
            if "u" in str(val) and float_val_check(val) >= 1.0:
                return 50  # Electrolytics rank
            return 30  # Small caps

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


def generate_field_manual(inventory, slots):
    pdf = FieldManual()

    for slot in slots:
        project_name = slot["name"]
        if not project_name:
            continue

        project_parts = []

        # Filter Inventory for this project
        for key, data in inventory.items():
            sources = data["sources"]

            # Check if this project uses this part
            # Note: sources keys might match slot['name']
            if project_name in sources:
                specific_refs = sources[project_name]
                qty = len(specific_refs)

                if qty > 0:
                    cat, val = key.split(" | ", 1)

                    is_polarized = cat in ["Diodes", "Transistors", "ICs"]
                    if cat == "Capacitors" and ("u" in val or "µ" in val):
                        is_polarized = True

                    project_parts.append(
                        {
                            "category": cat,
                            "value": val,
                            "qty": qty,
                            "refs": specific_refs,
                            "notes": "",  # Notes are harder to pull from 'buy_details' per project, leaving blank or generic
                            "polarized": is_polarized,
                        }
                    )

        # Sort
        sorted_parts = sort_by_z_height(project_parts)

        # Add to PDF
        pdf.add_project(project_name, sorted_parts)

    return bytes(pdf.output(dest="S"))
