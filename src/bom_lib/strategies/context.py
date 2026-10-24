"""Context orchestrator for BOM parsing strategies."""

import logging
import os
from urllib.parse import unquote, urlsplit

from src.bom_lib.enums import InputMethod
from src.bom_lib.fetch import FetchedDocument, FetchError, fetch_url
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult
from src.bom_lib.strategies.csv import CSVParserStrategy
from src.bom_lib.strategies.manual import ManualInputStrategy
from src.bom_lib.strategies.pdf import PDFParserStrategy
from src.bom_lib.types import (
    FileUpload,
    RawBOMData,
    create_empty_inventory,
    create_empty_stats,
)

logger = logging.getLogger(__name__)

_CSV_TYPES = frozenset({"text/csv", "text/tab-separated-values"})


def _from_download(doc: FetchedDocument) -> str | bytes | FileUpload:
    """What a downloaded document is parsed as: a PDF, a spreadsheet, or text.

    Raises:
        FetchError: The link answered with a web page instead of a document
            (Tonepad's file links open a download-warning page).
    """
    if doc.content.startswith(b"%PDF"):
        return doc.content
    if doc.content_type == "text/html" or doc.content.lstrip()[:1] == b"<":
        raise FetchError(
            "The link opened a web page, not a parts list. "
            "Download the PDF and upload it instead."
        )
    name = os.path.basename(unquote(urlsplit(doc.url).path))
    if doc.content_type in _CSV_TYPES or name.lower().endswith((".csv", ".tsv")):
        # Columns, not 'R1 10k' lines: pasted-text parsing would read
        # 'R1,10k,1' as a 10k,1 resistor.
        return FileUpload(name if "." in name else "download.csv", doc.content)
    if doc.content_type == "application/pdf" or name.lower().endswith(".pdf"):
        raise FetchError("The link answered with a damaged PDF.")
    return doc.content.decode("utf-8", errors="replace")


def _failed(error: str) -> ParseResult:
    """An empty reading that reports why it is empty."""
    stats = create_empty_stats()
    stats["errors"].append(error)
    return ParseResult(inventory=create_empty_inventory(), stats=stats)


class BOMParserContext:
    """Orchestrates strategy selection, I/O handling, and BOM parsing."""

    def __init__(self, strategies: list[BOMParserStrategy] | None = None) -> None:
        """Initialize the context with an ordered list of strategies.

        Args:
            strategies: Optional list of strategies. If None, defaults to
                        [PDFParserStrategy(), CSVParserStrategy(), ManualInputStrategy()].
        """
        if strategies is None:
            self.strategies: list[BOMParserStrategy] = [
                PDFParserStrategy(),
                CSVParserStrategy(),
                ManualInputStrategy(),
            ]
        else:
            self.strategies = list(strategies)

    def process(
        self, method: InputMethod, data: RawBOMData, source_name: str
    ) -> ParseResult:
        """Unified handler for processing Text, File, and URL inputs.

        Args:
            method: The input method enum (InputMethod.PASTE_TEXT, InputMethod.PRESET, etc.).
            data: The raw data associated with the method (String, UploadedFile, etc.).
            source_name: A display name for logging and error messages.

        Returns:
            A ParseResult containing inventory, stats, title, and raw_content.
        """
        if not data:
            return ParseResult(
                inventory=create_empty_inventory(),
                stats=create_empty_stats(),
                title=None,
                raw_content=None,
            )

        try:
            data_to_parse = data

            if method == InputMethod.FROM_URL:
                data_to_parse = _from_download(fetch_url(str(data)))

            for strategy in self.strategies:
                if strategy.can_handle(method, data_to_parse):
                    return strategy.parse(data_to_parse, source_name=source_name)

            return _failed("Unknown Method")

        except FetchError as e:
            # Written for the builder: a link the app won't or can't fetch.
            return _failed(str(e))
        except Exception:
            # A bug, not the builder's input: log it whole, and say so.
            logger.exception("Reading %s failed", source_name)
            return _failed(
                "Star Ground couldn't read this parts list because of a bug. "
                "It has been logged; please report it with the document."
            )
