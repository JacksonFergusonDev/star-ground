"""Offline checks injected into the production image without installing test tools."""

import csv
import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pdfplumber
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

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


def check_concurrent_reads() -> None:
    """Read several documents at once in this fresh process, as the API does.

    Shared parser state can break only under concurrency, and only before the
    process has parsed anything: pyparsing's parse-action probing once raced
    there and then failed every later parse.
    """
    uploads = []
    for i in range(8):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=16)
        for line in (
            f"Concurrent {i}",
            "Resistors",
            f"R1 {i + 1}k",
            "Capacitors",
            "C1 4n7",
        ):
            pdf.cell(0, 10, line, new_x="LMARGIN", new_y="NEXT")
        uploads.append(FileUpload(f"concurrent-{i}.pdf", bytes(pdf.output())))
    with ThreadPoolExecutor(max_workers=len(uploads)) as pool:
        readings = list(
            pool.map(lambda u: read_document(InputMethod.UPLOAD_FILE, u), uploads)
        )
    for reading in readings:
        assert reading is not None
        assert not reading.stats["errors"], reading.stats["errors"]
        assert reading.stats["parts_found"] == 2, reading.stats


def check_presets() -> None:
    """Require the catalog, and every typed parts list it names, to be packaged."""
    assert BOM_PRESETS, "Preset catalog is empty"
    for preset in BOM_PRESETS.values():
        assert preset["bom_text"].strip(), preset
        if source := preset.get("source_path"):
            assert Path(source).is_file(), source
        if url := preset.get("source_url"):
            assert url.startswith("https://"), url


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


def main() -> None:
    """Fail on any missing production capability, without network access."""
    assert os.getuid() != 0, "The production image must run as a non-root user"
    for check in (check_concurrent_reads, check_presets, check_backend, check_ocr):
        print(f"Running {check.__name__}", flush=True)
        check()
    print("All offline container checks passed", flush=True)


if __name__ == "__main__":
    main()
