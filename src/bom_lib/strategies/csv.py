"""Strategy for parsing CSV and tabular BOM uploads."""

import os
from typing import Any

from src.bom_lib.enums import InputMethod
from src.bom_lib.parser import parse_csv_bom
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult


class CSVParserStrategy(BOMParserStrategy):
    """Parses CSV BOM files by extracting contents and delegating to parse_csv_bom."""

    def can_handle(self, method: InputMethod, data: Any) -> bool:
        """Determine if this strategy can handle the given CSV/tabular input."""
        if hasattr(data, "name"):
            filename = str(data.name).lower()
            ext = os.path.splitext(filename)[1]
            if ext in [".csv", ".tsv", ".txt"]:
                return True
            if method == InputMethod.UPLOAD_FILE and ext != ".pdf":
                return True

        if isinstance(data, str) and data.lower().endswith((".csv", ".tsv")):
            return True

        return bool(
            method == InputMethod.UPLOAD_FILE
            and isinstance(data, (bytes, bytearray))
            and not data.startswith(b"%PDF")
        )

    def parse(self, data: Any, source_name: str) -> ParseResult:
        """Parse CSV data into a ParseResult, managing temporary files as needed.

        Args:
            data: File-like object (e.g. UploadedFile), file path string, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        # If data is an existing file path, parse directly without temp file creation
        if isinstance(data, str) and os.path.isfile(data):
            inventory, stats = parse_csv_bom(data, source_name=source_name)
            return ParseResult(inventory=inventory, stats=stats)

        ext = ".csv"
        if hasattr(data, "name"):
            filename = str(data.name)
            detected_ext = os.path.splitext(filename)[1]
            if detected_ext:
                ext = detected_ext.lower()

        raw_content = self._read_to_bytes(data, "CSVParserStrategy")
        with self._temp_file_from_bytes(raw_content, ext) as tmp_path:
            inventory, stats = parse_csv_bom(tmp_path, source_name=source_name)
            return ParseResult(inventory=inventory, stats=stats)
