from unittest.mock import patch

import pytest

from src.bom_lib import BOMParserContext, FileUpload, InputMethod, parse_pedalpcb_pdf
from src.bom_lib.errors import BUG_MESSAGE, TooManyPagesError, UnreadablePdfError

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore


@pytest.mark.skipif(pdfplumber is None, reason="pdfplumber not installed")
def test_a_file_that_isnt_a_pdf_is_explained_not_reported_as_a_bug():
    """A damaged file is the builder's to fix, so it's explained in plain words."""
    with (
        patch("pdfplumber.open", side_effect=Exception("Simulated PDF Corruption")),
        pytest.raises(UnreadablePdfError),
    ):
        parse_pedalpcb_pdf("fake_path.pdf", source_name="CrashTest")

    result = BOMParserContext().process(
        InputMethod.UPLOAD_FILE, FileUpload("damaged.pdf", b"%PDF-1.4 not really"), "X"
    )

    assert result.stats["errors"] == [UnreadablePdfError().as_read_error()]
    assert result.stats["errors"][0].bug is None


def test_a_pdf_longer_than_any_build_document_is_not_read(tmp_path):
    """Every page costs seconds of shared CPU, so a huge PDF is refused unread."""
    from fpdf import FPDF

    from src.bom_lib.parser import MAX_PDF_PAGES

    pdf = FPDF()
    for _ in range(MAX_PDF_PAGES + 1):
        pdf.add_page()
    path = tmp_path / "long.pdf"
    pdf.output(str(path))

    with (
        patch("src.bom_lib.parser.read_pages") as read_pages,
        pytest.raises(TooManyPagesError, match=f"has {MAX_PDF_PAGES + 1} pages"),
    ):
        parse_pedalpcb_pdf(str(path), source_name="Long")

    read_pages.assert_not_called()


def test_a_bug_while_parsing_a_pdf_is_reported_not_shown(tmp_path, caplog):
    """The builder sees a plain message; the traceback goes to the log and report."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "R1 10k")
    upload = FileUpload("Fuzz.pdf", bytes(pdf.output()))

    with patch(
        "src.bom_lib.parser.read_pages",
        side_effect=TypeError(
            "_eval_standard() missing 1 required positional argument"
        ),
    ):
        result = BOMParserContext().process(InputMethod.UPLOAD_FILE, upload, "X")

    (error,) = result.stats["errors"]
    assert error.message == BUG_MESSAGE
    bug = error.bug
    assert bug is not None
    assert "reading the upload Fuzz.pdf" in bug.detail
    assert "_eval_standard" in bug.detail
    assert f"Bug {bug.code}" in caplog.text
