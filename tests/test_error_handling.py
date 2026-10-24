from unittest.mock import patch

import pytest

from src.bom_lib import parse_pedalpcb_pdf

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore


@pytest.mark.skipif(pdfplumber is None, reason="pdfplumber not installed")
def test_pdf_parser_catches_critical_errors():
    """
    Verifies that the PDF parser catches critical exceptions and reports them
    in stats['errors'] instead of crashing the app.
    """
    # We simulate a "Corrupt File" scenario where opening the PDF raises an error
    with patch("pdfplumber.open", side_effect=Exception("Simulated PDF Corruption")):
        # We can pass a dummy path because the mock intercepts the call before file access
        stats = parse_pedalpcb_pdf("fake_path.pdf", source_name="CrashTest").stats

        # ASSERTION 1: The app did not crash (implicit if we reach this line)

        # ASSERTION 2: The error was recorded in the new 'errors' list
        assert len(stats["errors"]) == 1
        assert "Simulated PDF Corruption" in stats["errors"][0]

        # ASSERTION 3: The function returned a safe, empty state
        assert stats["parts_found"] == 0
        assert stats["lines_read"] == 0


def test_a_pdf_longer_than_any_build_document_is_not_read(tmp_path):
    """Every page costs seconds of shared CPU, so a huge PDF is refused unread."""
    from fpdf import FPDF

    from src.bom_lib.parser import MAX_PDF_PAGES

    pdf = FPDF()
    for _ in range(MAX_PDF_PAGES + 1):
        pdf.add_page()
    path = tmp_path / "long.pdf"
    pdf.output(str(path))

    with patch("src.bom_lib.parser.read_pages") as read_pages:
        stats = parse_pedalpcb_pdf(str(path), source_name="Long").stats

    read_pages.assert_not_called()
    assert stats["errors"] == [
        f"The PDF has {MAX_PDF_PAGES + 1} pages. Build documents have at most "
        f"{MAX_PDF_PAGES}, so it wasn't read."
    ]
