from unittest.mock import patch

import pytest

from star_ground.bom_lib import (
    BOMParserContext,
    FileUpload,
    InputMethod,
    parse_pdf,
)
from star_ground.bom_lib.errors import (
    BUG_MESSAGE,
    ReadError,
    TooManyPagesError,
    UnreadablePdfError,
)
from tests.pdfs import assemble, stream, text_pdf

try:
    import pdfplumber
except ImportError:
    pdfplumber = None  # type: ignore


def test_a_file_that_isnt_a_pdf_is_explained_not_reported_as_a_bug(tmp_path):
    """A damaged file is the builder's to fix, so it's explained in plain words."""
    path = tmp_path / "damaged.pdf"
    path.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(UnreadablePdfError):
        parse_pdf(str(path), source_name="CrashTest")
    with pytest.raises(UnreadablePdfError):
        parse_pdf(str(tmp_path / "missing.pdf"), source_name="CrashTest")

    result = BOMParserContext().process(
        InputMethod.UPLOAD_FILE, FileUpload("damaged.pdf", b"%PDF-1.4 not really"), "X"
    )

    assert result.stats.errors == [UnreadablePdfError().as_read_error()]
    assert result.stats.errors[0].bug is None


def test_a_pdf_longer_than_any_build_document_is_not_read(tmp_path):
    """Every page costs seconds of shared CPU, so a huge PDF is refused unread."""
    from star_ground.bom_lib.pdf.guard import MAX_PDF_PAGES

    path = tmp_path / "long.pdf"
    path.write_bytes(text_pdf(pages=MAX_PDF_PAGES + 1))

    with (
        patch("star_ground.bom_lib.pdf.document.read_pages") as read_pages,
        pytest.raises(TooManyPagesError, match=f"has {MAX_PDF_PAGES + 1} pages"),
    ):
        parse_pdf(str(path), source_name="Long")

    read_pages.assert_not_called()


def test_a_bug_while_parsing_a_pdf_is_reported_not_shown(tmp_path, caplog):
    """The builder sees a plain message; the traceback goes to the log and report."""
    upload = FileUpload("Fuzz.pdf", text_pdf("R1 10k"))

    with patch(
        "star_ground.bom_lib.pdf.document.read_pages",
        side_effect=TypeError(
            "_eval_standard() missing 1 required positional argument"
        ),
    ):
        result = BOMParserContext().process(InputMethod.UPLOAD_FILE, upload, "X")

    (error,) = result.stats.errors
    assert error.message == BUG_MESSAGE
    bug = error.bug
    assert bug is not None
    assert f"Bug {bug.code}" in caplog.text
    assert "reading the upload Fuzz.pdf" in caplog.text
    assert "_eval_standard" in caplog.text


def _one_page(
    page: str, resources: str = "", draws: bytes = b"", *more: bytes
) -> bytes:
    """A one-page PDF listing 'R1 10k'.

    Args:
        page: The page dictionary's own entries, such as its MediaBox.
        resources: More entries for its resources ('/XObject << ... >>').
        draws: More of its content stream, after the listing ('/Fm0 Do').
        more: More objects, numbered from 6.
    """
    return assemble(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R {page} /Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R >> {resources} >> >>".encode(),
        stream(b"BT /F1 10 Tf 50 700 Td (R1 10k) Tj ET " + draws),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *more,
    )


def _read(pdf: bytes) -> ReadError:
    (error,) = (
        BOMParserContext()
        .process(InputMethod.UPLOAD_FILE, FileUpload("damaged.pdf", pdf), "X")
        .stats.errors
    )
    return error


_LETTER = "/MediaBox [0 0 612 792]"


@pytest.mark.parametrize(
    "page",
    [
        pytest.param("/MediaBox [0 0 (a) 792]", id="box-of-text"),
        pytest.param("", id="no-box"),
        pytest.param("/MediaBox [0 0 612]", id="box-of-three"),
        pytest.param("/MediaBox 5", id="box-of-a-number"),
        pytest.param("/MediaBox 9 0 R", id="box-missing-object"),
        pytest.param(f"{_LETTER} /CropBox [0 0 1]", id="crop-box-of-three"),
        pytest.param(f"{_LETTER} /Rotate (a)", id="rotation-of-text"),
    ],
)
def test_a_page_with_no_readable_size_is_explained(tmp_path, page):
    """pdfplumber reads each page's size when listing the pages, after opening."""
    path = tmp_path / "damaged.pdf"
    path.write_bytes(_one_page(page))
    with pytest.raises(UnreadablePdfError):
        parse_pdf(str(path), "Damaged")

    error = _read(path.read_bytes())
    assert error == UnreadablePdfError().as_read_error()
    assert error.bug is None


def test_a_page_with_a_readable_size_is_read(tmp_path):
    path = tmp_path / "fine.pdf"
    path.write_bytes(_one_page(f"{_LETTER} /CropBox [0 0 612 792] /Rotate 0"))
    result = parse_pdf(str(path), "Fine")
    assert [ref for part in result.inventory.values() for ref in part.refs] == ["R1"]


def test_a_page_pdfminer_cant_draw_is_explained(tmp_path):
    """pdfminer parses a page's drawing only when its contents are first read."""
    # The page draws a form whose matrix is text, which pdfminer can't multiply.
    form = stream(
        b"BT /F1 10 Tf (R2 22k) Tj ET",
        "/Subtype /Form /BBox [0 0 9 9] /Matrix [(a) 0 0 1 0 0] ",
    )
    path = tmp_path / "damaged.pdf"
    path.write_bytes(_one_page(_LETTER, "/XObject << /Fm0 6 0 R >>", b"/Fm0 Do", form))
    with pytest.raises(UnreadablePdfError):
        parse_pdf(str(path), "Damaged")
    assert _read(path.read_bytes()).bug is None
