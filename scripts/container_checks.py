"""Offline checks injected into the production image without installing test tools."""

import csv
import datetime
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pdfplumber
import typst
from PIL import Image, ImageDraw, ImageFont

import star_ground.exports
from star_ground import version
from star_ground.api import Project, generate_master_list, read_document, read_stock
from star_ground.bom_lib import FileUpload, InputMethod
from star_ground.bom_lib.ocr import recognize
from star_ground.exports.pack import PackContents, build_pack
from star_ground.exports.spreadsheets import shopping_list_csv

FONTS = Path(star_ground.exports.__file__).parent / "fonts"


def text_pdf(*lines: str) -> bytes:
    """A PDF of the given lines, typeset with the image's own Typst and fonts."""
    body = "\n\n".join(f"#{json.dumps(line)}" for line in lines)
    source = f'#set text(font: "DM Sans 14pt", size: 16pt)\n{body}'
    pdf: bytes = typst.compile(  # type: ignore[misc]
        source.encode(), font_paths=[str(FONTS)], ignore_system_fonts=True
    )
    return pdf


def check_backend() -> None:
    """Parse a PDF, subtract stock, and validate CSV, PDF and ZIP exports."""
    upload = FileUpload(
        "smoke.pdf",
        text_pdf("Container Smoke", "Resistors", "R1 10k", "Capacitors", "C1 100n"),
    )
    reading = read_document(InputMethod.UPLOAD_FILE, upload)
    assert reading is not None
    assert reading.stats.parts_found == 2, reading
    stock = read_stock(FileUpload("stock.csv", b"Category,Part,Qty\nResistors,10k,1\n"))
    master = generate_master_list(
        [Project(name="Smoke", count=3, method=InputMethod.UPLOAD_FILE, data=upload)],
        stock=stock,
    )
    assert not master.stats.errors, master.stats
    rows = master.shopping_list()
    resistor = next(row for row in rows if row.part == "10k")
    assert (resistor.bom_qty, resistor.in_stock, resistor.net_need) == (
        3,
        1,
        2,
    )
    shopping = shopping_list_csv(rows)
    exported = list(csv.DictReader(io.StringIO(shopping.decode("utf-8-sig"))))
    assert next(row for row in exported if row["Part"] == "10k")["Still needed"] == "2"
    made = datetime.date(2026, 1, 1)
    contents = PackContents(
        master.kit, master.projects, rows, master.stock, False, made, version()
    )
    folder = "Star Ground build pack 2026-01-01"
    with zipfile.ZipFile(io.BytesIO(build_pack(contents))) as pack:
        assert pack.testzip() is None
        assert pack.read(f"{folder}/Shopping list.csv") == shopping
        assert pack.read(f"{folder}/Source documents/Smoke.pdf") == upload.content
        for name in ("Build book.pdf", "Labels (Avery 5160).pdf"):
            with pdfplumber.open(io.BytesIO(pack.read(f"{folder}/{name}"))) as document:
                assert document.pages, name
                assert document.pages[0].extract_text(), name
    # The image is installed as a package, so it knows its own version.
    assert version() != "dev", version()


def check_concurrent_reads() -> None:
    """Read several documents at once in this fresh process, as the API does.

    Shared parser state can break only under concurrency, and only before the
    process has parsed anything: pyparsing's parse-action probing once raced
    there and then failed every later parse.
    """
    uploads = []
    for i in range(8):
        pdf = text_pdf(
            f"Concurrent {i}", "Resistors", f"R1 {i + 1}k", "Capacitors", "C1 4n7"
        )
        uploads.append(FileUpload(f"concurrent-{i}.pdf", pdf))
    with ThreadPoolExecutor(max_workers=len(uploads)) as pool:
        readings = list(
            pool.map(lambda u: read_document(InputMethod.UPLOAD_FILE, u), uploads)
        )
    for reading in readings:
        assert reading is not None
        assert not reading.stats.errors, reading.stats.errors
        assert reading.stats.parts_found == 2, reading.stats


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
    # The host formats these messages after Docker returns its captured output.
    for name, check in (
        ("Concurrent PDF reads", check_concurrent_reads),
        ("Parts lists, inventory and exports", check_backend),
        ("OCR inference", check_ocr),
    ):
        print(f"Checking {name}", flush=True)
        check()


if __name__ == "__main__":
    main()
