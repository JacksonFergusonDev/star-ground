from unittest.mock import patch

import pytest

from src.bom_lib.parser import parse_pedalpcb_pdf

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
        inventory, stats = parse_pedalpcb_pdf("fake_path.pdf", source_name="CrashTest")

        # ASSERTION 1: The app did not crash (implicit if we reach this line)

        # ASSERTION 2: The error was recorded in the new 'errors' list
        assert len(stats["errors"]) == 1
        assert "Simulated PDF Corruption" in stats["errors"][0]

        # ASSERTION 3: The function returned a safe, empty state
        assert stats["parts_found"] == 0
        assert stats["lines_read"] == 0
