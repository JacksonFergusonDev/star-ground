"""Live fetches of real build documents, one per publisher.

These reach the internet, so they are deselected by default (`-m "not
network"` in pyproject.toml). Run them before a release, or when a publisher's
site changes: `uv run pytest -m network` (or `just test-network`).
"""

import pytest

from src.bom_lib import InputMethod
from src.bom_lib.errors import FetchError
from src.bom_lib.fetch import fetch_url
from src.bom_lib.strategies.context import BOMParserContext

pytestmark = pytest.mark.network

DOCUMENTS = {
    "PedalPCB": "https://docs.pedalpcb.com/project/Parentheses.pdf",
    "GuitarPCB": "https://guitarpcb.com/wp-content/uploads/2022/08/69-Stones-Drive-v5.pdf",
    "Aion FX": "https://aionfx.com/app/files/docs/360_preamp_documentation.pdf",
    "Five Cats": (
        "https://www.five-cats-pedals.co.uk/wp-content/uploads/2024/06/"
        "FCP-24-Carat-Fuzz-Insert-V1-0.pdf"
    ),
}


@pytest.mark.parametrize("url", DOCUMENTS.values(), ids=DOCUMENTS.keys())
def test_each_publishers_documents_download_and_parse(url: str) -> None:
    result = BOMParserContext().process(InputMethod.FROM_URL, url, "Live")
    assert result.stats["errors"] == []
    assert (result.raw_content or b"").startswith(b"%PDF")
    # Five Cats prints its parts list as an image: OCR's reading is offered.
    assert result.stats["parts_found"] > 0 or result.ocr is not None


def test_tonepad_links_are_explained() -> None:
    """Tonepad's file links open a download-warning page, not the PDF."""
    result = BOMParserContext().process(
        InputMethod.FROM_URL, "https://tonepad.com/getFile.asp?id=102", "Live"
    )
    assert "Download the PDF" in result.stats["errors"][0]


def test_a_github_file_page_fetches_the_file() -> None:
    url = (
        "https://github.com/JacksonFergusonDev/star-ground/blob/main/"
        "raw_boms/tayda/fuzz/big_muff.txt"
    )
    result = BOMParserContext().process(InputMethod.FROM_URL, url, "Live")
    assert result.stats["errors"] == []
    assert result.stats["parts_found"] > 10


def test_a_private_address_behind_a_public_name_is_refused() -> None:
    # localtest.me resolves to 127.0.0.1 for everyone.
    with pytest.raises(FetchError, match="not a public address"):
        fetch_url("http://localtest.me/")
