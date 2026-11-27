# ruff: noqa
# mypy: ignore-errors
import sys, time, logging, collections, functools

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
logging.disable(logging.CRITICAL)
import pdfium_page as P

P.install()
import star_ground.bom_lib.pdf.document as d

acc = collections.Counter()


def wrap(mod, name, label):
    f = getattr(mod, name)

    @functools.wraps(f)
    def g(*a, **k):
        t = time.perf_counter()
        try:
            return f(*a, **k)
        finally:
            acc[label] += time.perf_counter() - t

    setattr(mod, name, g)


wrap(P, "_chars", "pdfium chars")
wrap(P, "_paths", "pdfium paths")
wrap(P, "_images", "pdfium images")
wrap(d, "_without_overprint", "_without_overprint (incl. its chars)")
wrap(d, "_tables", "_tables")
wrap(d, "_page_words", "_page_words")
wrap(d, "read_pages", "read_pages")
wrap(d, "_extract", "_extract total")
from pathlib import Path

pdfs = sorted(Path("tests/samples").glob("*/*.pdf"))
d.parse_pdf(str(pdfs[0]), "S")
acc.clear()
t = time.perf_counter()
for p in pdfs[1:]:
    d.parse_pdf(str(p), "S")
tot = time.perf_counter() - t
print(f"total {tot:.2f}s")
for k, v in acc.most_common():
    print(f"{v:6.2f}s {100 * v / tot:5.1f}% {k}")
