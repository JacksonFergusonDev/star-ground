r"""Adds documents checked by hand to the catalog's verified list.

A document's parts are checked by labelling them: a file in the corpus's
`golden/` format, transcribed from the document (`tests/accuracy.py`
describes it). This reads each labelled document exactly as the catalog job
does (`src.catalog.build.catalog_reading`), grades that reading against the
labels with the accuracy suite's scorer, and adds the file to
`catalog/verified.json` only when every part of every build is right. The
entry holds the file's SHA-256 and the reading's fingerprint, so the mark
lasts only while both stay the same (`src.catalog.verified`).

A document that doesn't match is reported with its first wrong part and
left off. Entries for documents not in this run are kept.

Usage:
    uv run python -m tools.verify_documents \\
        --corpus PDF_DIR:GOLDEN_DIR [--corpus ...] [--out catalog/verified.json]
"""

import argparse
import hashlib
import sys
from datetime import date
from multiprocessing import Pool
from pathlib import Path

from src.api import read_document
from src.bom_lib import FileUpload, InputMethod, OcrVerdict
from src.catalog.build import catalog_reading
from src.catalog.verified import Checked, VerifiedList, fingerprint
from tests.accuracy import GoldenDoc, load_golden, score_result


def _verify(doc: GoldenDoc) -> tuple[str, str, str | None, str]:
    """One labelled document: its name, hash, fingerprint if right, and why not."""
    content = doc.pdf.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    reading = read_document(InputMethod.UPLOAD_FILE, FileUpload(doc.pdf.name, content))
    if reading is None:
        return doc.name, digest, None, "not a document Star Ground reads"
    listed = catalog_reading(reading)
    ocr = reading.ocr
    used_ocr = ocr is not None and ocr.has_parts and ocr.verdict == OcrVerdict.COMPLETE
    score = score_result(doc, listed, ocr if used_ocr else None)
    if score.failures:
        first = score.failures[0]
        return (
            doc.name,
            digest,
            None,
            f"{len(score.failures)} wrong, first {first.describe()}",
        )
    return doc.name, digest, fingerprint(listed), ""


def main(argv: list[str] | None = None) -> int:
    """Grades every labelled document and records the ones read right."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--corpus",
        action="append",
        required=True,
        help="PDF_DIR:GOLDEN_DIR, labelled documents and their PDFs.",
    )
    parser.add_argument("--out", type=Path, default=Path("catalog/verified.json"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    docs: list[GoldenDoc] = []
    for spec in args.corpus:
        pdf_dir, golden_dir = (Path(d) for d in spec.split(":", 1))
        for path in sorted(golden_dir.glob("*.json")):
            doc = load_golden(path, pdf_dir)
            if doc.pdf.exists():
                docs.append(doc)
            else:
                print(f"skipped {path.name}: {doc.pdf} isn't here", file=sys.stderr)

    with Pool(args.workers) as pool:
        graded = pool.map(_verify, docs, chunksize=4)

    verified = VerifiedList.load(args.out)
    today = date.today().isoformat()
    added = 0
    for name, digest, parts, why in graded:
        if parts is None:
            print(f"not verified  {name}: {why}")
            continue
        if verified.checked.get(digest, Checked("", "")).parts != parts:
            added += 1
        verified.checked[digest] = Checked(parts, today)
    verified.save(args.out)
    right = sum(parts is not None for _, _, parts, _ in graded)
    print(
        f"{right} of {len(graded)} labelled documents read right; {added} added or "
        f"refreshed; {len(verified.checked)} in {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
