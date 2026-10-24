"""Strategy for parsing CSV and tabular BOM uploads."""

import os

from src.bom_lib.enums import InputMethod
from src.bom_lib.parser import parse_csv_bom
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult
from src.bom_lib.types import RawBOMData, SupportsName


class CSVParserStrategy(BOMParserStrategy):
    """Parses CSV BOM files by extracting contents and delegating to parse_csv_bom."""

    def can_handle(self, method: InputMethod, data: RawBOMData) -> bool:
        """Determine if this strategy can handle the given CSV/tabular input."""
        if isinstance(data, SupportsName):
            filename = str(data.name).lower()
            ext = os.path.splitext(filename)[1]
            if ext in (".csv", ".tsv"):
                return True
            # A .txt is 'R1 10k' lines (`ManualInputStrategy`).
            if method == InputMethod.UPLOAD_FILE and ext not in (".pdf", ".txt"):
                return True

        return bool(
            method == InputMethod.UPLOAD_FILE
            and isinstance(data, (bytes, bytearray))
            and not data.startswith(b"%PDF")
        )

    def parse(self, data: RawBOMData, source_name: str) -> ParseResult:
        """Parse CSV data into a ParseResult, managing temporary files as needed.

        A string is never read as a file path (see `PDFParserStrategy.parse`).

        Args:
            data: An upload (`FileUpload`), file-like object, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        ext = ".csv"
        if isinstance(data, SupportsName):
            filename = str(data.name)
            detected_ext = os.path.splitext(filename)[1]
            if detected_ext:
                ext = detected_ext.lower()

        raw_content = self._read_to_bytes(data, "CSVParserStrategy")
        with self._temp_file_from_bytes(raw_content, ext) as tmp_path:
            return parse_csv_bom(tmp_path, source_name=source_name)
