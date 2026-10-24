"""Strategy for parsing manual text inputs and presets."""

from src.bom_lib.enums import InputMethod
from src.bom_lib.parser import parse_with_verification
from src.bom_lib.strategies.base import BOMParserStrategy, ParseResult
from src.bom_lib.types import FileUpload, RawBOMData

TEXT_SUFFIX = ".txt"


def _is_text_upload(data: RawBOMData) -> bool:
    return isinstance(data, FileUpload) and data.name.lower().endswith(TEXT_SUFFIX)


class ManualInputStrategy(BOMParserStrategy):
    """Parses raw text BOM lines from manual copy-paste or pre-configured presets."""

    def can_handle(self, method: InputMethod, data: RawBOMData) -> bool:
        """Determine if this strategy can handle manual text input."""
        if method == InputMethod.UPLOAD_FILE:
            return _is_text_upload(data)
        if method in (InputMethod.PASTE_TEXT, InputMethod.PRESET, InputMethod.FROM_URL):
            return isinstance(data, (str, list))
        return False

    def parse(self, data: RawBOMData, source_name: str) -> ParseResult:
        """Parse manual text input into a ParseResult.

        Args:
            data: Raw text, a list of lines, or an uploaded .txt file.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        if isinstance(data, FileUpload):
            bom_list = [data.content.decode("utf-8-sig", errors="replace")]
        elif isinstance(data, list):
            bom_list = [str(item) for item in data]
        else:
            bom_list = [str(data)]

        return parse_with_verification(bom_list, source_name=source_name)
