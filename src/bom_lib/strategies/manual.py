"""Strategy for parsing manual text inputs and presets."""

from typing import Any

from src.bom_lib.parser import parse_with_verification
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult


class ManualInputStrategy(BOMParserStrategy):
    """Parses raw text BOM lines from manual copy-paste or pre-configured presets."""

    def can_handle(self, method: str, data: Any) -> bool:
        """Determine if this strategy can handle manual text input."""
        if method in ["Paste Text", "Preset"]:
            return True
        return bool(isinstance(data, (str, list)) and not method)

    def parse(self, data: Any, source_name: str) -> ParseResult:
        """Parse manual text input into a ParseResult.

        Args:
            data: Raw text string or list of text strings.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        if isinstance(data, list):
            bom_list = [str(item) for item in data]
        else:
            bom_list = [str(data)]

        inventory, stats = parse_with_verification(bom_list, source_name=source_name)
        return ParseResult(inventory=inventory, stats=stats)
