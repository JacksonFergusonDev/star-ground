"""Context orchestrator for BOM parsing strategies."""

import logging
from typing import Any

import requests

from src.bom_lib.enums import InputMethod
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult
from src.bom_lib.strategies.csv import CSVParserStrategy
from src.bom_lib.strategies.manual import ManualInputStrategy
from src.bom_lib.strategies.pdf import PDFParserStrategy
from src.bom_lib.types import create_empty_inventory

logger = logging.getLogger(__name__)


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

    def register_strategy(self, strategy: BOMParserStrategy) -> None:
        """Register a new strategy.

        Args:
            strategy: The BOMParserStrategy instance to register.
        """
        self.strategies.append(strategy)

    def process(self, method: InputMethod, data: Any, source_name: str) -> ParseResult:
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
                stats={
                    "lines_read": 0,
                    "parts_found": 0,
                    "residuals": [],
                    "extracted_title": None,
                    "seen_refs": set(),
                    "errors": [],
                },
                title=None,
                raw_content=None,
            )

        try:
            data_to_parse = data

            if method == InputMethod.FROM_URL:
                url = str(data).strip()
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                is_pdf = url.lower().endswith(".pdf") or response.content.startswith(
                    b"%PDF"
                )
                data_to_parse = response.content if is_pdf else response.text

            for strategy in self.strategies:
                if strategy.can_handle(method, data_to_parse):
                    return strategy.parse(data_to_parse, source_name=source_name)

            return ParseResult(
                inventory=create_empty_inventory(),
                stats={
                    "lines_read": 0,
                    "parts_found": 0,
                    "residuals": [],
                    "extracted_title": None,
                    "seen_refs": set(),
                    "errors": ["Unknown Method"],
                },
                title=None,
                raw_content=None,
            )

        except Exception as e:
            logger.error(f"Error processing {source_name}: {e}")
            return ParseResult(
                inventory=create_empty_inventory(),
                stats={
                    "lines_read": 0,
                    "parts_found": 0,
                    "residuals": [],
                    "extracted_title": None,
                    "seen_refs": set(),
                    "errors": [str(e)],
                },
                title=None,
                raw_content=None,
            )
