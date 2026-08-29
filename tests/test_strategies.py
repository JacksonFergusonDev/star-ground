"""Unit tests for BOM parsing strategies."""

import os
import tempfile
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from src.bom_lib.strategies import (
    CSVParserStrategy,
    ManualInputStrategy,
    ParseResult,
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
        assert strategy.can_handle("From URL", "https://example.com/test.pdf") is False
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
