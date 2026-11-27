# ruff: noqa
# mypy: ignore-errors
import sys, time, logging, traceback

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from pathlib import Path

logging.disable(logging.CRITICAL)
import star_ground.bom_lib.pdf.document as d
import pdfium_page

pdfs = sorted(Path("tests/samples").glob("*/*.pdf"))


def sig(r):
    inv = {str(k): (v.qty, sorted(v.refs)) for k, v in r.inventory.items()}
    return {
        "inv": inv,
        "title": r.title,
        "warn": sorted((w.kind.name, w.ref) for w in r.stats.warnings),
        "variants": [v.name for v in r.variants],
    }


base = {}
t = time.perf_counter()
for p in pdfs:
    base[p.name] = sig(d.parse_pdf(str(p), "S"))
tb = time.perf_counter() - t
orig, new = pdfium_page.install()
res = {}
t = time.perf_counter()
for p in pdfs:
    try:
        res[p.name] = sig(new(str(p), "S"))
    except Exception as e:
        res[p.name] = f"ERR {type(e).__name__}: {e}"
        traceback.print_exc(limit=3)
tn = time.perf_counter() - t
print(f"pdfminer {tb:.2f}s  pdfium {tn:.2f}s")
same = 0
for k in base:
    r = res[k]
    if r == base[k]:
        same += 1
        continue
    if isinstance(r, str):
        print("FAIL", k, r[:150])
        continue
    ib, ir = base[k]["inv"], r["inv"]
    print(
        f"DIFF {k}: parts {len(ib)}->{len(ir)}; missing {len(set(ib) - set(ir))}, extra {len(set(ir) - set(ib))}; title {base[k]['title']!r}->{r['title']!r}; warn {len(base[k]['warn'])}->{len(r['warn'])}; variants {len(base[k]['variants'])}->{len(r['variants'])}"
    )
print(f"identical: {same}/{len(base)}")
