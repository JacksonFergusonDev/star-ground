"""Offline checks injected into the production image without installing test tools."""

import csv
import io
import os
import zipfile
from pathlib import Path

import pdfplumber
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
from streamlit.testing.v1 import AppTest

from src.api import Project, generate_master_list, read_document, read_stock
from src.bom_lib import BOM_PRESETS, FileUpload, InputMethod
from src.bom_lib.ocr import recognize
from src.exporters import generate_shopping_list_csv, generate_stock_update_csv


def check_backend() -> None:
    """Parse a PDF, subtract stock, and validate CSV, PDF and ZIP exports."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=16)
    for line in ("Container Smoke", "Resistors", "R1 10k", "Capacitors", "C1 100n"):
        pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
    upload = FileUpload("smoke.pdf", bytes(pdf.output()))
    reading = read_document(InputMethod.UPLOAD_FILE, upload)
    assert reading is not None
    assert reading.stats["parts_found"] == 2, reading
    stock = read_stock(FileUpload("stock.csv", b"Category,Part,Qty\nResistors,10k,1\n"))
    master = generate_master_list(
        [Project(name="Smoke", count=3, method=InputMethod.UPLOAD_FILE, data=upload)],
        stock=stock,
    )
    assert not master.stats["errors"], master.stats
    rows = master.shopping_list()
    resistor = next(row for row in rows if row["Part"] == "10k")
    assert (resistor["BOM Qty"], resistor["In Stock"], resistor["Net Need"]) == (
        3,
        1,
        2,
    )
    shopping = generate_shopping_list_csv(rows)
    exported = list(csv.DictReader(io.StringIO(shopping.decode("utf-8-sig"))))
    assert next(row for row in exported if row["Part"] == "10k")["Net Need"] == "2"
    stock_csv = generate_stock_update_csv(rows)
    with zipfile.ZipFile(io.BytesIO(master.build_pack(shopping, stock_csv))) as pack:
        assert pack.testzip() is None
        assert pack.read("Shopping List.csv") == shopping
        assert pack.read("Source Documents/Smoke Source.pdf") == upload.content
        for name in (
            "Field Manuals/Smoke Field Manual.pdf",
            "Sticker Sheets/Smoke Sticker Sheet.pdf",
        ):
            with pdfplumber.open(io.BytesIO(pack.read(name))) as document:
                assert document.pages, name
                assert document.pages[0].extract_text(), name


def check_presets() -> None:
    """Require the catalog and every referenced source document to be packaged."""
    assert BOM_PRESETS, "Preset catalog is empty"
    for preset in BOM_PRESETS.values():
        assert preset["bom_text"].strip(), preset
        if source := preset.get("source_path"):
            assert Path(source).is_file(), source


def check_ocr() -> None:
    """Run the production detector and recognizer on a deterministic text image."""
    image = Image.new("RGB", (1000, 240), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=60)
    draw.text((50, 50), "R1 10k", font=font, fill="black")
    boxes = recognize(image, 1.0)
    text = " ".join(box.text for box in boxes)
    assert "R1" in text, text
    assert "10k" in text, text


def check_app() -> None:
    """Execute the actual app and generate a master list through its widgets."""
    app = AppTest.from_file("/app/app.py", default_timeout=60).run()
    assert not app.exception, app.exception
    assert app.title[0].value == "⚡ Star Ground"
    app.text_area[0].set_value("R1 10k\nC1 100n\nU1 TL072").run()
    next(
        button for button in app.button if button.label == "Generate Master List"
    ).click().run()
    assert not app.exception, app.exception
    assert not app.error, app.error
    assert app.metric[1].value == "3"
    assert "TL072" in app.dataframe[0].value["Part"].values
    assert len(app.get("download_button")) == 5


def main() -> None:
    """Fail on any missing production capability, without network access."""
    assert os.getuid() != 0, "The production image must run as a non-root user"
    for check in (check_presets, check_backend, check_ocr, check_app):
        print(f"Running {check.__name__}", flush=True)
        check()
    print("All offline container checks passed", flush=True)


if __name__ == "__main__":
    main()
