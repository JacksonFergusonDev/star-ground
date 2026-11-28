# ruff: noqa
# mypy: ignore-errors
import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pdfium_page

pdfium_page.install()
exec(open(str(__import__("pathlib").Path(__file__).parent / "cold.py")).read())
