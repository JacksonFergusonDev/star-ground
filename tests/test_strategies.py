"""Unit tests for BOM parsing strategies."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.bom_lib.strategies import (
    BOMParserContext,
    CSVParserStrategy,
    ManualInputStrategy,
    ParseResult,
    PDFParserStrategy,
)


@dataclass
class MockUploadedFile:
    """Mock representing Streamlit's UploadedFile object."""

    name: str
    _buffer: bytes

    def getvalue(self) -> bytes:
        return self._buffer

    def read(self) -> bytes:
        return self._buffer


class TestManualInputStrategy:
    """Tests for ManualInputStrategy."""

    def test_can_handle(self) -> None:
        strategy = ManualInputStrategy()

        assert strategy.can_handle("Paste Text", "R1 10k") is True
        assert strategy.can_handle("Preset", "R1 10k") is True
        assert strategy.can_handle("", "R1 10k") is True
        assert strategy.can_handle("", ["R1 10k"]) is True
        assert strategy.can_handle("From URL", "R1 10k") is True
        assert strategy.can_handle("Upload File", b"%PDF-1.4") is False

    def test_parse_string(self) -> None:
        strategy = ManualInputStrategy()
        raw_text = "R1 10k\nR2 100k\nC1 100n"

        result = strategy.parse(raw_text, source_name="TestPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 3
        assert result.stats["lines_read"] == 3
        assert "Resistors | 10k" in result.inventory
        assert result.inventory["Resistors | 10k"]["qty"] == 1
        assert "R1" in result.inventory["Resistors | 10k"]["refs"]
        assert result.title is None
        assert result.raw_content is None

    def test_parse_list_of_strings(self) -> None:
        strategy = ManualInputStrategy()
        lines = ["R1 10k", "R2 100k"]

        result = strategy.parse(lines, source_name="TestPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 2
        assert "Resistors | 10k" in result.inventory
        assert "Resistors | 100k" in result.inventory


class TestCSVParserStrategy:
    """Tests for CSVParserStrategy."""

    def test_can_handle(self) -> None:
        strategy = CSVParserStrategy()

        csv_file = MockUploadedFile(name="bom.csv", _buffer=b"")
        tsv_file = MockUploadedFile(name="bom.tsv", _buffer=b"")
        pdf_file = MockUploadedFile(name="bom.pdf", _buffer=b"%PDF-1.4")
        other_file = MockUploadedFile(name="bom.unknown", _buffer=b"")

        assert strategy.can_handle("Upload File", csv_file) is True
        assert strategy.can_handle("Upload File", tsv_file) is True
        assert strategy.can_handle("Upload File", pdf_file) is False
        assert strategy.can_handle("Upload File", other_file) is True

        assert strategy.can_handle("", "project/bom.csv") is True
        assert strategy.can_handle("", "project/bom.tsv") is True
        assert strategy.can_handle("", "project/bom.pdf") is False

        assert strategy.can_handle("Upload File", b"Designator,Value\nR1,10k") is True
        assert strategy.can_handle("Upload File", b"%PDF-1.4\nsome pdf") is False

    def test_parse_uploaded_file(self) -> None:
        strategy = CSVParserStrategy()
        csv_bytes = b"Designator,Value\nR1,10k\nR2,100k\nC1,47n\n"
        mock_file = MockUploadedFile(name="pedal_bom.csv", _buffer=csv_bytes)

        result = strategy.parse(mock_file, source_name="CSVPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 3
        assert "Resistors | 10k" in result.inventory
        assert result.inventory["Resistors | 10k"]["qty"] == 1
        assert "R1" in result.inventory["Resistors | 10k"]["refs"]

    def test_parse_raw_bytes(self) -> None:
        strategy = CSVParserStrategy()
        csv_bytes = b"Ref,Val\nR1,10k\n"

        result = strategy.parse(csv_bytes, source_name="BytesPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert "Resistors | 10k" in result.inventory

    def test_parse_file_path(self, tmp_path: Any) -> None:
        strategy = CSVParserStrategy()
        csv_file = tmp_path / "test.csv"
        csv_file.write_text("Ref,Value\nR1,10k\n", encoding="utf-8")

        result = strategy.parse(str(csv_file), source_name="PathPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert "Resistors | 10k" in result.inventory

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
            strategy.parse(12345, source_name="Invalid")


class TestPDFParserStrategy:
    """Tests for PDFParserStrategy."""

    @pytest.fixture
    def sample_pdf_path(self) -> str:
        pdf_path = Path("tests/samples/clean/Cataclysm-PedalPCB.pdf")
        assert pdf_path.exists(), f"Sample PDF not found at {pdf_path}"
        return str(pdf_path)

    def test_can_handle(self) -> None:
        strategy = PDFParserStrategy()

        pdf_file = MockUploadedFile(name="pedal.pdf", _buffer=b"")
        csv_file = MockUploadedFile(name="pedal.csv", _buffer=b"")

        assert strategy.can_handle("Upload File", pdf_file) is True
        assert strategy.can_handle("Upload File", csv_file) is False

        assert strategy.can_handle("", "path/to/pedal.pdf") is True
        assert strategy.can_handle("", "path/to/pedal.PDF") is True
        assert strategy.can_handle("", "path/to/pedal.csv") is False

        assert strategy.can_handle("Upload File", b"%PDF-1.4 binary data") is True
        assert strategy.can_handle("Upload File", b"Designator,Value\nR1,10k") is False

    def test_parse_file_path(self, sample_pdf_path: str) -> None:
        strategy = PDFParserStrategy()

        result = strategy.parse(sample_pdf_path, source_name="Cataclysm")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] > 0
        assert result.title is not None
        assert "Cataclysm" in result.title
        assert result.raw_content is not None
        assert result.raw_content.startswith(b"%PDF")

    def test_parse_uploaded_file(self, sample_pdf_path: str) -> None:
        strategy = PDFParserStrategy()
        with open(sample_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        mock_file = MockUploadedFile(name="Cataclysm-PedalPCB.pdf", _buffer=pdf_bytes)
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
            strategy.parse(12345, source_name="Invalid")


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

    def test_custom_strategies_and_register(self) -> None:
        context = BOMParserContext([ManualInputStrategy()])
        assert len(context.strategies) == 1
        context.register_strategy(CSVParserStrategy())
        assert len(context.strategies) == 2

    def test_process_empty_data(self) -> None:
        context = BOMParserContext()
        result = context.process("Paste Text", "", "Empty")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert result.stats["lines_read"] == 0
        assert len(result.inventory) == 0

    def test_process_paste_text(self) -> None:
        context = BOMParserContext()
        result = context.process("Paste Text", "R1 10k\nC1 100n", "PastePedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 2
        assert "Resistors | 10k" in result.inventory
        assert "Capacitors | 100n" in result.inventory

    def test_process_preset(self) -> None:
        context = BOMParserContext()
        result = context.process("Preset", "R1 10k", "PresetPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert "Resistors | 10k" in result.inventory

    def test_process_upload_file_csv(self) -> None:
        context = BOMParserContext()
        mock_file = MockUploadedFile(
            name="build.csv", _buffer=b"Designator,Value\nR1,10k\n"
        )
        result = context.process("Upload File", mock_file, "CSVPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 1
        assert "Resistors | 10k" in result.inventory

    def test_process_upload_file_pdf(self, sample_pdf_bytes: bytes) -> None:
        context = BOMParserContext()
        mock_file = MockUploadedFile(name="Cataclysm.pdf", _buffer=sample_pdf_bytes)
        result = context.process("Upload File", mock_file, "PDFPedal")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] > 0
        assert result.title is not None
        assert result.raw_content == sample_pdf_bytes

    def test_process_from_url_pdf(self, sample_pdf_bytes: bytes) -> None:
        context = BOMParserContext()

        mock_resp = MagicMock()
        mock_resp.content = sample_pdf_bytes
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = context.process(
                "From URL", "https://example.com/Cataclysm.pdf", "URLPedal"
            )

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] > 0
        assert result.title is not None
        assert result.raw_content == sample_pdf_bytes

    def test_process_from_url_text(self) -> None:
        context = BOMParserContext()

        mock_resp = MagicMock()
        mock_resp.content = b"R1 10k\nR2 100k\n"
        mock_resp.text = "R1 10k\nR2 100k\n"
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp):
            result = context.process(
                "From URL", "https://example.com/raw_bom.txt", "URLPedal"
            )

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 2
        assert "Resistors | 10k" in result.inventory

    def test_process_from_url_network_error(self) -> None:
        context = BOMParserContext()

        with patch(
            "requests.get",
            side_effect=requests.RequestException("Connection timed out"),
        ):
            result = context.process(
                "From URL", "https://example.com/bad.pdf", "ErrPedal"
            )

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert len(result.stats["errors"]) == 1
        assert "Connection timed out" in result.stats["errors"][0]

    def test_process_unknown_method(self) -> None:
        context = BOMParserContext([])
        result = context.process("NonExistentMethod", 12345, "Unknown")

        assert isinstance(result, ParseResult)
        assert result.stats["parts_found"] == 0
        assert len(result.stats["errors"]) == 1
        assert result.stats["errors"][0] == "Unknown Method"
