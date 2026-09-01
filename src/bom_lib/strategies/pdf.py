"""Strategy for parsing PedalPCB PDF build documents."""

import os
import tempfile
from typing import Any

from src.bom_lib.enums import InputMethod
from src.bom_lib.parser import parse_pedalpcb_pdf
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult


class PDFParserStrategy(BOMParserStrategy):
    """Parses PDF BOM files by extracting contents and delegating to parse_pedalpcb_pdf."""

    def can_handle(self, method: InputMethod, data: Any) -> bool:
        """Determine if this strategy can handle PDF inputs via extension or magic bytes."""
        if hasattr(data, "name") and str(data.name).lower().endswith(".pdf"):
            return True

        if isinstance(data, str) and data.lower().endswith(".pdf"):
            return True

        return bool(
            isinstance(data, (bytes, bytearray)) and bytes(data).startswith(b"%PDF")
        )

    def parse(self, data: Any, source_name: str) -> ParseResult:
        """Parse PDF data into a ParseResult, managing temporary files as needed.

        Args:
            data: File-like object (e.g. UploadedFile), file path string, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory, statistics, extracted title, and raw PDF bytes.
        """
        if isinstance(data, str) and os.path.isfile(data):
            with open(data, "rb") as f:
                raw_bytes = f.read()
            inventory, stats = parse_pedalpcb_pdf(data, source_name=source_name)
            return ParseResult(
                inventory=inventory,
                stats=stats,
                title=stats.get("extracted_title"),
                raw_content=raw_bytes,
            )

        if hasattr(data, "getvalue"):
            raw_content = data.getvalue()
        elif hasattr(data, "read"):
            raw_content = data.read()
        elif isinstance(data, (bytes, bytearray)):
            raw_content = bytes(data)
        else:
            raise ValueError(
                f"Unsupported data type for PDFParserStrategy: {type(data)}"
            )

        if isinstance(raw_content, str):
            raw_content = raw_content.encode("utf-8")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(raw_content)
            tmp_path = tmp.name

        try:
            inventory, stats = parse_pedalpcb_pdf(tmp_path, source_name=source_name)
            return ParseResult(
                inventory=inventory,
                stats=stats,
                title=stats.get("extracted_title"),
                raw_content=raw_content,
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
