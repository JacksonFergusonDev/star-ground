"""Base classes for the BOM parser strategy pattern."""

import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager

from src.bom_lib.enums import InputMethod
from src.bom_lib.types import (
    ParseResult,
    RawBOMData,
    SupportsGetValue,
    SupportsRead,
)

__all__ = ["BOMParserStrategy", "ParseResult"]


class BOMParserStrategy(ABC):
    """Abstract base class for all BOM parsing strategies."""

    @abstractmethod
    def can_handle(self, method: InputMethod, data: RawBOMData) -> bool:
        """Determine if this strategy can parse the given input data."""
        pass

    @staticmethod
    def _read_to_bytes(data: RawBOMData, strategy_name: str) -> bytes:
        if data is None:
            raise ValueError(f"Unsupported data type for {strategy_name}: None")
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        elif isinstance(data, str):
            raw = data.encode("utf-8")
        elif isinstance(data, SupportsGetValue):
            val = data.getvalue()
            raw = (
                bytes(val)
                if isinstance(val, (bytes, bytearray))
                else str(val).encode("utf-8")
            )
        elif isinstance(data, SupportsRead):
            val = data.read()
            raw = (
                bytes(val)
                if isinstance(val, (bytes, bytearray))
                else str(val).encode("utf-8")
            )
        else:
            raise ValueError(f"Unsupported data type for {strategy_name}: {type(data)}")
        return raw

    @staticmethod
    @contextmanager
    def _temp_file_from_bytes(data: bytes, suffix: str) -> Generator[str]:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            yield tmp_path
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @abstractmethod
    def parse(self, data: RawBOMData, source_name: str) -> ParseResult:
        """Parse the given input data into a standard ParseResult."""
        pass
