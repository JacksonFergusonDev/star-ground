r"""Measures what finding designators on boards and schematics would cost.

Step 7 of `PLAN.md` lights each part up on the document's board render or
schematic. Where those are vector drawings with real text (Aion FX, Tonepad),
the positions come free from the text layer. Where they are embedded images
(PedalPCB, GuitarPCB, Five Cats), they need OCR, which costs the API's CPU.
This tool measures both, per document, against the corpus's labeled parts:

- **Text layer:** how many listed designators are printed more than once (the
  parts list, and the board or schematic), an upper bound on what's free.
- **Images:** each distinct embedded image covering at least
  `--min-share` of its page, rendered at the parts-list OCR's 200 dpi and read
  upright, then turned 90 degrees and read again (RapidOCR turns only crops
  taller than 1.5:1, so short sideways labels like 'R1' need the second pass).
  For each pass, the designators found and the CPU seconds it took.

CPU time is the process's (`time.process_time`), which counts every OCR
thread. It's measured on this machine; Cloud Run's vCPUs are slower, so compare
with the parts-list OCR's figures in `PLAN.md` measured on both.

Usage:
    uv run python -m tools.measure_board_ocr OUT.json \\
        --corpus pedalpcb=PDFS:GOLDEN [--corpus ...] [--sample 10]
"""

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from PIL import Image

from src.bom_lib.ocr import recognize

# The parts-list OCR's resolution: it reads 'R27' reliably at this size.
_DPI = 200
_REPEAT = re.compile(r" \(\d+\)$")
_TOKEN = re.compile(r"[A-Z][A-Z0-9_]*\d[A-Z0-9_]*|[A-Z]{3,}")


@dataclass
class ImageResult:
    """One embedded image, read upright and turned.

    Attributes:
        page: Its page, from 0.
        share: Its share of the page's area.
        pixels: The rendered crop's width times height.
        upright: Listed designators read upright.
        turned: Listed designators read only when turned 90 degrees.
        upright_cpu: CPU seconds reading it upright.
        turned_cpu: CPU seconds reading it turned.
    """

    page: int
    share: float
    pixels: int
    upright: list[str]
    turned: list[str]
    upright_cpu: float
    turned_cpu: float


@dataclass
class DocumentResult:
    """One document's measurements.

    Attributes:
        publisher: Its publisher.
        pdf: Its file name.
        listed: How many designators its parts list has.
        text_layer: Listed designators printed more than once in its text.
        images: Each distinct image read.
        render_cpu: CPU seconds rendering the images.
    """

    publisher: str
    pdf: str
    listed: int
    text_layer: list[str]
    images: list[ImageResult] = field(default_factory=list)
    render_cpu: float = 0.0


def _listed(golden: Path) -> set[str]:
    """The designators a labeled document lists, across every build."""
    labeled = json.loads(golden.read_text())
    builds = labeled.get("variants") or {"": {"parts": labeled["parts"]}}
    return {
        _REPEAT.sub("", ref).upper()
        for build in builds.values()
        for ref in build["parts"]
    }


def _named(texts: Iterable[str], listed: set[str]) -> set[str]:
    return {t for text in texts for t in _TOKEN.findall(text.upper())} & listed


def _image_key(image: dict[str, Any]) -> str:
    stream = image.get("stream")
    data = stream.get_rawdata() if stream is not None else b""
    return hashlib.sha256(data or repr(image["srcsize"]).encode()).hexdigest()


def _read(image: Image.Image, scale: float, listed: set[str]) -> tuple[set[str], float]:
    start = time.process_time()
    boxes = recognize(image, scale)
    return _named((b.text for b in boxes), listed), time.process_time() - start


def measure(
    publisher: str, pdf: Path, golden: Path, min_share: float
) -> DocumentResult:
    """Measures one document.

    Args:
        publisher: Its publisher, for the report.
        pdf: The document.
        golden: Its labeled parts.
        min_share: The smallest share of a page an image must cover.

    Returns:
        The measurements.
    """
    listed = _listed(golden)
    with pdfplumber.open(pdf) as doc:
        counts: dict[str, int] = {}
        for page in doc.pages:
            for word in page.extract_words():
                for ref in _named([word["text"]], listed):
                    counts[ref] = counts.get(ref, 0) + 1
        result = DocumentResult(
            publisher,
            pdf.name,
            len(listed),
            sorted(ref for ref, n in counts.items() if n > 1),
        )
        seen: set[str] = set()
        for number, page in enumerate(doc.pages):
            area = float(page.width * page.height)
            for image in page.images:
                x0, top = max(image["x0"], 0), max(image["top"], 0)
                x1, bottom = (
                    min(image["x1"], page.width),
                    min(image["bottom"], page.height),
                )
                share = (x1 - x0) * (bottom - top) / area
                key = _image_key(image)
                if share < min_share or key in seen:
                    continue
                seen.add(key)
                start = time.process_time()
                crop = page.crop((x0, top, x1, bottom)).to_image(resolution=_DPI)
                rendered = crop.original.convert("RGB")
                result.render_cpu += time.process_time() - start
                scale = 72 / _DPI
                upright, upright_cpu = _read(rendered, scale, listed)
                turned, turned_cpu = _read(
                    rendered.rotate(90, expand=True), scale, listed
                )
                result.images.append(
                    ImageResult(
                        number,
                        round(share, 3),
                        rendered.width * rendered.height,
                        sorted(upright),
                        sorted(turned - upright),
                        round(upright_cpu, 3),
                        round(turned_cpu, 3),
                    )
                )
    return result


def _share(found: list[set[str]], docs: list[DocumentResult]) -> str:
    """The median document's share of its listed designators found."""
    shares = [len(f) / max(r.listed, 1) for f, r in zip(found, docs, strict=True)]
    return f"{statistics.median(shares):.0%}"


def _spread(values: list[float]) -> str:
    return f"{statistics.median(values):.1f} ({max(values):.1f} max)"


def _summary(results: list[DocumentResult]) -> str:
    rows = [
        "| Publisher | Docs | Images/doc | Text layer | Upright | +Turned "
        "| CPU s upright | CPU s both |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for publisher in sorted({r.publisher for r in results}):
        docs = [r for r in results if r.publisher == publisher]
        upright = [{ref for i in r.images for ref in i.upright} for r in docs]
        both = [
            u | {ref for i in r.images for ref in i.turned}
            for u, r in zip(upright, docs, strict=True)
        ]
        cpu_up = [r.render_cpu + sum(i.upright_cpu for i in r.images) for r in docs]
        cpu_both = [
            c + sum(i.turned_cpu for i in r.images)
            for c, r in zip(cpu_up, docs, strict=True)
        ]
        images = statistics.median(len(r.images) for r in docs)
        text = _share([set(r.text_layer) for r in docs], docs)
        rows.append(
            f"| {publisher} | {len(docs)} | {images:.0f} | {text} "
            f"| {_share(upright, docs)} | {_share(both, docs)} "
            f"| {_spread(cpu_up)} | {_spread(cpu_both)} |"
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> None:
    """Measures a sample of each corpus and writes the results as JSON."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--corpus",
        action="append",
        required=True,
        help="PUBLISHER=PDF_DIR:GOLDEN_DIR",
    )
    parser.add_argument("--sample", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-share", type=float, default=0.08)
    args = parser.parse_args(argv)

    results: list[DocumentResult] = []
    for spec in args.corpus:
        publisher, dirs = spec.split("=", 1)
        pdf_dir, golden_dir = (Path(d) for d in dirs.split(":", 1))
        goldens = sorted(Path(golden_dir).glob("*.json"))
        for golden in random.Random(args.seed).sample(
            goldens, min(args.sample, len(goldens))
        ):
            pdf = pdf_dir / json.loads(golden.read_text())["pdf"]
            if not pdf.exists():
                continue
            results.append(measure(publisher, pdf, golden, args.min_share))
            print(f"{publisher} {pdf.name}", file=sys.stderr)
            args.out.write_text(json.dumps([asdict(r) for r in results], indent=1))
    print(_summary(results))


if __name__ == "__main__":
    main()
