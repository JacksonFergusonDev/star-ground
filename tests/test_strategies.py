"""Unit tests for BOM parsing strategies."""

import os
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from src.bom_lib.enums import ComponentCategory, InputMethod
from src.bom_lib.errors import BUG_MESSAGE, FetchError
from src.bom_lib.fetch import FetchedDocument
from src.bom_lib.strategies import (
    BOMParserContext,
    CSVParserStrategy,
    ManualInputStrategy,
    ParseResult,
    PDFParserStrategy,
)
from src.bom_lib.types import ComponentKey, FileUpload


class TestManualInputStrategy:
    """Tests for ManualInputStrategy."""

    def test_can_handle(self) -> None:
        strategy = ManualInputStrategy()

        assert strategy.can_handle(InputMethod.PASTE_TEXT, "R1 10k") is True
        assert strategy.can_handle(InputMethod.PRESET, "R1 10k") is True
        assert strategy.can_handle(InputMethod.FROM_URL, "R1 10k") is True
        assert strategy.can_handle(InputMethod.PRESET, ["R1 10k"]) is True
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, b"%PDF-1.4") is False
        txt = FileUpload("bom.txt", b"R1 10k")
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, txt) is True
        assert CSVParserStrategy().can_handle(InputMethod.UPLOAD_FILE, txt) is False

    def test_an_uploaded_text_file_is_read_as_lines(self) -> None:
        upload = FileUpload("bom.txt", b"R1 10k\nR2 22k\nC1 100n\n")

        result = BOMParserContext().process(InputMethod.UPLOAD_FILE, upload, "Txt")

        assert result.stats["parts_found"] == 3

    def test_parse_string(self) -> None:
        strategy = ManualInputStrategy()
        raw_text = "R1 10k\nR2 100k\nC1 100n"

        result = strategy.parse(raw_text, source_name="TestPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 3
        assert result.stats["lines_read"] == 3
        k_10k = ComponentKey(ComponentCategory.RESISTORS, "10k")
        assert k_10k in result.inventory
        assert result.inventory[k_10k]["qty"] == 1
        assert "R1" in result.inventory[k_10k]["refs"]
        assert result.title is None
        assert result.raw_content is None

    def test_parse_list_of_strings(self) -> None:
        strategy = ManualInputStrategy()
        lines = ["R1 10k", "R2 100k"]

        result = strategy.parse(lines, source_name="TestPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 2
        assert ComponentKey(ComponentCategory.RESISTORS, "10k") in result.inventory
        assert ComponentKey(ComponentCategory.RESISTORS, "100k") in result.inventory


class TestCSVParserStrategy:
    """Tests for CSVParserStrategy."""

    def test_can_handle(self) -> None:
        strategy = CSVParserStrategy()

        csv_file = FileUpload(name="bom.csv", content=b"")
        tsv_file = FileUpload(name="bom.tsv", content=b"")
        pdf_file = FileUpload(name="bom.pdf", content=b"%PDF-1.4")
        other_file = FileUpload(name="bom.unknown", content=b"")

        assert strategy.can_handle(InputMethod.UPLOAD_FILE, csv_file) is True
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, tsv_file) is True
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, pdf_file) is False
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, other_file) is True

        assert strategy.can_handle(InputMethod.PASTE_TEXT, "project/bom.csv") is False

        assert (
            strategy.can_handle(InputMethod.UPLOAD_FILE, b"Designator,Value\nR1,10k")
            is True
        )
        assert (
            strategy.can_handle(InputMethod.UPLOAD_FILE, b"%PDF-1.4\nsome pdf") is False
        )

    def test_parse_uploaded_file(self) -> None:
        strategy = CSVParserStrategy()
        csv_bytes = b"Designator,Value\nR1,10k\nR2,100k\nC1,47n\n"
        mock_file = FileUpload(name="pedal_bom.csv", content=csv_bytes)

        result = strategy.parse(mock_file, source_name="CSVPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 3
        k_10k = ComponentKey(ComponentCategory.RESISTORS, "10k")
        assert k_10k in result.inventory
        assert result.inventory[k_10k]["qty"] == 1
        assert "R1" in result.inventory[k_10k]["refs"]

    def test_parse_raw_bytes(self) -> None:
        strategy = CSVParserStrategy()
        csv_bytes = b"Ref,Val\nR1,10k\n"

        result = strategy.parse(csv_bytes, source_name="BytesPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert ComponentKey(ComponentCategory.RESISTORS, "10k") in result.inventory

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("bom.tsv", b"Ref\tValue\nR1\t10k\nC1\t100n\n"),
            # Excel where the decimal mark is a comma.
            ("bom.csv", b"Ref;Value\nR1;4,7k\nC1;100n\n"),
            # Excel on Windows saves CSV as Windows-1252.
            ("bom.csv", "Ref,Value\nR1,10k\nC1,100n\n".encode("cp1252")),
            ("bom.csv", "Ref,Value,Note\nR1,10k,µ\nC1,100n,é\n".encode("cp1252")),
        ],
    )
    def test_spreadsheet_exports_are_read(self, name: str, content: bytes) -> None:
        result = CSVParserStrategy().parse(FileUpload(name, content), "Sheet")

        assert result.stats["parts_found"] == 2
        assert result.stats["residuals"] == []

    def test_parse_temp_file_cleanup(self) -> None:
        strategy = CSVParserStrategy()
        csv_bytes = b"Ref,Val\nR1,10k\n"

        created_temp_files: list[str] = []
        original_named_temp_file = tempfile.NamedTemporaryFile

        def track_temp_file(*args: Any, **kwargs: Any) -> Any:
            tmp = original_named_temp_file(*args, **kwargs)
            created_temp_files.append(tmp.name)
            return tmp

        with patch("tempfile.NamedTemporaryFile", side_effect=track_temp_file):
            result = strategy.parse(csv_bytes, source_name="CleanupTest")

        assert result.stats["parts_found"] == 1
        assert len(created_temp_files) == 1
        assert not os.path.exists(created_temp_files[0])

    def test_parse_unsupported_type_raises(self) -> None:
        strategy = CSVParserStrategy()
        with pytest.raises(ValueError, match="Unsupported data type"):
            strategy.parse(cast(Any, 12345), source_name="Invalid")


class TestPDFParserStrategy:
    """Tests for PDFParserStrategy."""

    @pytest.fixture
    def sample_pdf_path(self) -> str:
        pdf_path = Path("tests/samples/clean/Cataclysm-PedalPCB.pdf")
        assert pdf_path.exists(), f"Sample PDF not found at {pdf_path}"
        return str(pdf_path)

    def test_can_handle(self) -> None:
        strategy = PDFParserStrategy()

        pdf_file = FileUpload(name="pedal.pdf", content=b"")
        csv_file = FileUpload(name="pedal.csv", content=b"")

        assert strategy.can_handle(InputMethod.UPLOAD_FILE, pdf_file) is True
        assert strategy.can_handle(InputMethod.UPLOAD_FILE, csv_file) is False

        assert strategy.can_handle(InputMethod.PASTE_TEXT, "path/to/pedal.pdf") is False

        assert (
            strategy.can_handle(InputMethod.UPLOAD_FILE, b"%PDF-1.4 binary data")
            is True
        )
        assert (
            strategy.can_handle(InputMethod.UPLOAD_FILE, b"Designator,Value\nR1,10k")
            is False
        )

    def test_parse_uploaded_file(self, sample_pdf_path: str) -> None:
        strategy = PDFParserStrategy()
        with open(sample_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        mock_file = FileUpload(name="Cataclysm-PedalPCB.pdf", content=pdf_bytes)
        result = strategy.parse(mock_file, source_name="Cataclysm")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] > 0
        assert result.title is not None
        assert result.raw_content == pdf_bytes

    def test_parse_raw_bytes_and_cleanup(self, sample_pdf_path: str) -> None:
        strategy = PDFParserStrategy()
        with open(sample_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        created_temp_files: list[str] = []
        original_named_temp_file = tempfile.NamedTemporaryFile

        def track_temp_file(*args: Any, **kwargs: Any) -> Any:
            tmp = original_named_temp_file(*args, **kwargs)
            created_temp_files.append(tmp.name)
            return tmp

        with patch("tempfile.NamedTemporaryFile", side_effect=track_temp_file):
            result = strategy.parse(pdf_bytes, source_name="Cataclysm")

        assert result.stats["parts_found"] > 0
        assert len(created_temp_files) == 1
        assert not os.path.exists(created_temp_files[0])

    def test_parse_unsupported_type_raises(self) -> None:
        strategy = PDFParserStrategy()
        with pytest.raises(ValueError, match="Unsupported data type"):
            strategy.parse(cast(Any, 12345), source_name="Invalid")


class TestBOMParserContext:
    """Tests for BOMParserContext."""

    @pytest.fixture
    def sample_pdf_bytes(self) -> bytes:
        pdf_path = Path("tests/samples/clean/Cataclysm-PedalPCB.pdf")
        assert pdf_path.exists(), f"Sample PDF not found at {pdf_path}"
        with open(pdf_path, "rb") as f:
            return f.read()

    def test_default_strategies_registered(self) -> None:
        context = BOMParserContext()
        assert len(context.strategies) == 3
        assert isinstance(context.strategies[0], PDFParserStrategy)
        assert isinstance(context.strategies[1], CSVParserStrategy)
        assert isinstance(context.strategies[2], ManualInputStrategy)

    def test_custom_strategies(self) -> None:
        context = BOMParserContext([ManualInputStrategy()])
        assert len(context.strategies) == 1

    def test_process_empty_data(self) -> None:
        context = BOMParserContext()
        result = context.process(InputMethod.PASTE_TEXT, "", "Empty")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert result.stats["lines_read"] == 0
        assert len(result.inventory) == 0

    def test_process_paste_text(self) -> None:
        context = BOMParserContext()
        result = context.process(
            InputMethod.PASTE_TEXT, "R1 10k\nC1 100n", "PastePedal"
        )

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 2
        assert ComponentKey(ComponentCategory.RESISTORS, "10k") in result.inventory
        assert ComponentKey(ComponentCategory.CAPACITORS, "100n") in result.inventory

    def test_process_preset(self) -> None:
        context = BOMParserContext()
        result = context.process(InputMethod.PRESET, "R1 10k", "PresetPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert ComponentKey(ComponentCategory.RESISTORS, "10k") in result.inventory

    def test_process_upload_file_csv(self) -> None:
        context = BOMParserContext()
        mock_file = FileUpload(name="build.csv", content=b"Designator,Value\nR1,10k\n")
        result = context.process(InputMethod.UPLOAD_FILE, mock_file, "CSVPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert ComponentKey(ComponentCategory.RESISTORS, "10k") in result.inventory

    def test_process_upload_file_pdf(self, sample_pdf_bytes: bytes) -> None:
        context = BOMParserContext()
        mock_file = FileUpload(name="Cataclysm.pdf", content=sample_pdf_bytes)
        result = context.process(InputMethod.UPLOAD_FILE, mock_file, "PDFPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] > 0
        assert result.title is not None
        assert result.raw_content == sample_pdf_bytes

    @pytest.mark.parametrize("name", ["bom.pdf", "bom.csv", "bom.tsv"])
    def test_pasted_text_is_never_read_as_a_server_path(
        self, tmp_path: Path, sample_pdf_bytes: bytes, name: str
    ) -> None:
        # A path typed into the text box must not open a file on the server
        # (its bytes would come back in the build pack).
        secret = tmp_path / name
        secret.write_bytes(
            sample_pdf_bytes if name.endswith(".pdf") else b"Ref,Value\nR1,10k\n"
        )

        for method in (InputMethod.PASTE_TEXT, InputMethod.PRESET):
            result = BOMParserContext().process(method, str(secret), "Pasted")
            assert result.raw_content is None
            assert result.stats["parts_found"] == 0

    def test_a_downloaded_path_is_never_read_as_a_server_path(
        self, tmp_path: Path, sample_pdf_bytes: bytes
    ) -> None:
        secret = tmp_path / "bom.pdf"
        secret.write_bytes(sample_pdf_bytes)
        doc = FetchedDocument(str(secret).encode(), "text/plain", "https://x.test/a")

        with patch("src.bom_lib.strategies.context.fetch_url", return_value=doc):
            result = BOMParserContext().process(
                InputMethod.FROM_URL, "https://x.test/a", "Linked"
            )

        assert result.raw_content is None
        assert result.stats["parts_found"] == 0

    def test_process_from_url_network_error(self) -> None:
        context = BOMParserContext()

        with patch(
            "src.bom_lib.strategies.context.fetch_url",
            side_effect=FetchError("example.com took too long to answer."),
        ):
            result = context.process(
                InputMethod.FROM_URL, "https://example.com/bad.pdf", "ErrPedal"
            )

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert len(result.stats["errors"]) == 1
        assert result.stats["errors"] == ["example.com took too long to answer."]

    def test_a_bug_is_logged_and_reported_as_one(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        strategy = ManualInputStrategy()
        with patch.object(strategy, "parse", side_effect=KeyError("oops")):
            result = BOMParserContext([strategy]).process(
                InputMethod.PASTE_TEXT, "R1 10k", "Buggy"
            )

        assert result.stats["errors"] == [BUG_MESSAGE]
        (bug,) = result.bugs
        assert "reading Buggy" in bug.detail
        assert "KeyError" in bug.detail  # with its traceback
        assert f"Bug {bug.code}" in caplog.text

    def test_process_unknown_method(self) -> None:
        context = BOMParserContext([])
        result = context.process(InputMethod.PASTE_TEXT, "12345", "Unknown")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert len(result.stats["errors"]) == 1
        assert result.stats["errors"][0] == "Unknown Method"
