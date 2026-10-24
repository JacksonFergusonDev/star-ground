"""Strategy for parsing PedalPCB PDF build documents."""

from dataclasses import replace

from src.bom_lib.enums import InputMethod
from src.bom_lib.ocr import attempt_ocr
from src.bom_lib.parser import parse_pedalpcb_pdf
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult
from src.bom_lib.types import RawBOMData, SupportsName


def _parse(path: str, source_name: str) -> ParseResult:
    """Parses a PDF, and reads its image pages by OCR when the parse looks incomplete.

    The OCR parts are offered on the result (`ParseResult.ocr`), not added.
    """
    res = parse_pedalpcb_pdf(path, source_name=source_name)
    return replace(res, ocr=attempt_ocr(path, res, source_name))


class PDFParserStrategy(BOMParserStrategy):
    """Parses PDF BOM files by extracting contents and delegating to parse_pedalpcb_pdf."""

    def can_handle(self, method: InputMethod, data: RawBOMData) -> bool:
        """Determine if this strategy can handle PDF inputs via extension or magic bytes."""
        if isinstance(data, SupportsName) and str(data.name).lower().endswith(".pdf"):
            return True

        return bool(
            isinstance(data, (bytes, bytearray)) and bytes(data).startswith(b"%PDF")
        )

    def parse(self, data: RawBOMData, source_name: str) -> ParseResult:
        """Parse PDF data into a ParseResult, managing temporary files as needed.

        A string is never read as a file path: it is text the builder typed,
        so it can't name a file on the server.

        Args:
            data: An upload (`FileUpload`), file-like object, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory, statistics, extracted title, and raw PDF bytes.
        """
        raw_content = self._read_to_bytes(data, "PDFParserStrategy")
        with self._temp_file_from_bytes(raw_content, ".pdf") as tmp_path:
            res = _parse(tmp_path, source_name)
            return replace(res, raw_content=raw_content)
