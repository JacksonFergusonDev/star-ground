"""Typesets a document's contents into a PDF with Typst.

The templates in `templates/` lay out what `documents.py` decides. They read
the contents as JSON from `sys.inputs.data`, and use only the fonts in
`fonts/`, so a PDF comes out the same on every machine.
"""

import json
from dataclasses import asdict
from pathlib import Path

import typst

from star_ground.exports.documents import BuildBook, LabelSheet

_HERE = Path(__file__).parent
_TEMPLATES = _HERE / "templates"
_FONTS = _HERE / "fonts"


def _typeset(template: str, contents: BuildBook | LabelSheet) -> bytes:
    pdf: bytes = typst.compile(
        str(_TEMPLATES / template),
        root=str(_TEMPLATES),
        font_paths=[str(_FONTS)],
        ignore_system_fonts=True,
        sys_inputs={"data": json.dumps(asdict(contents))},
    )
    return pdf


def build_book_pdf(book: BuildBook) -> bytes:
    """The build book as a PDF.

    Args:
        book: Its contents.

    Returns:
        The PDF's bytes.
    """
    return _typeset("book.typ", book)


def label_sheet_pdf(sheet: LabelSheet) -> bytes:
    """The labels as a PDF for Avery 5160 sheets.

    Args:
        sheet: Its labels.

    Returns:
        The PDF's bytes.
    """
    return _typeset("labels.typ", sheet)
