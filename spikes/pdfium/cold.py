# ruff: noqa
# mypy: ignore-errors
import time, logging, sys
from pathlib import Path

logging.disable(logging.CRITICAL)
from star_ground.bom_lib.pdf.document import parse_pdf

pdfs = sorted(Path("tests/samples").glob("*/*.pdf"))
parse_pdf(str(pdfs[0]), "S")  # warm lazy imports only (one doc)
t = time.perf_counter()
for p in pdfs[1:]:
    parse_pdf(str(p), "S")
print(time.perf_counter() - t)
