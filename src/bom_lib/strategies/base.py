"""Base classes for the BOM parser strategy pattern."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.bom_lib.enums import InputMethod
from src.bom_lib.types import Inventory, StatsDict


@dataclass
class ParseResult:
    """The result of parsing a BOM source."""

    inventory: Inventory
    stats: StatsDict
    title: str | None = None
    raw_content: bytes | None = None


class BOMParserStrategy(ABC):
    """Abstract base class for all BOM parsing strategies."""

    @abstractmethod
    def can_handle(self, method: InputMethod, data: Any) -> bool:
        """Determine if this strategy can parse the given input data.

        Args:
            method: The UI method enum (e.g., InputMethod.PASTE_TEXT, InputMethod.UPLOAD_FILE).
            data: The raw input data to inspect.

        Returns:
            True if this strategy can handle the input, False otherwise.
        """
        pass

    @abstractmethod
    def parse(self, data: Any, source_name: str) -> ParseResult:
        """Parse the given input data into a standard ParseResult.

        Args:
            data: The input data to parse.
            source_name: The name or identifier of the source (e.g., filename).

        Returns:
            A ParseResult containing the extracted inventory and statistics.
        """
        pass
