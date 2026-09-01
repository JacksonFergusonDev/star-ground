"""Strategy for parsing CSV and tabular BOM uploads."""

import os
import tempfile
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

        if hasattr(data, "getvalue"):
            raw_content = data.getvalue()
        elif hasattr(data, "read"):
            raw_content = data.read()
        elif isinstance(data, (bytes, bytearray)):
            raw_content = bytes(data)
        elif isinstance(data, str):
            raw_content = data.encode("utf-8")
        else:
            raise ValueError(
                f"Unsupported data type for CSVParserStrategy: {type(data)}"
            )

        if isinstance(raw_content, str):
            raw_content = raw_content.encode("utf-8")

        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(raw_content)
            tmp_path = tmp.name

        try:
            inventory, stats = parse_csv_bom(tmp_path, source_name=source_name)
            return ParseResult(inventory=inventory, stats=stats)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
