"""Small PDFs for tests, typeset with Typst and Star Ground's own fonts."""

import json
from pathlib import Path

import typst

FONTS = Path(__file__).parent.parent / "src" / "star_ground" / "exports" / "fonts"


def typeset(source: str) -> bytes:
    """A PDF from Typst markup, using only the fonts the exports embed."""
    # typst's stubs type `input` and `font_paths` with one type variable,
    # which bytes and a path string can't share.
    pdf: bytes = typst.compile(  # type: ignore[misc]
        source.encode(), font_paths=[str(FONTS)], ignore_system_fonts=True
    )
    return pdf


def text_pdf(*lines: str, pages: int = 1, size: int = 14) -> bytes:
    """A US Letter PDF with each line as its own paragraph on the first page.

    Args:
        lines: The text, line by line.
        pages: How many pages in all; the rest are blank.
        size: The text's size in points.

    Returns:
        The PDF's bytes.
    """
    body = "\n\n".join(f"#{json.dumps(line)}" for line in lines)
    breaks = "\n#pagebreak()\n" * (pages - 1)
    return typeset(
        '#set page(paper: "us-letter")\n'
        f'#set text(font: "DM Sans 14pt", size: {size}pt)\n'
        f"{body}{breaks}"
    )
