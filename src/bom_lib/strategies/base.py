"""Base classes for the BOM parser strategy pattern."""

import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from src.bom_lib.enums import InputMethod
from src.bom_lib.types import Inventory, StatsDict


@dataclass
class ParseResult:
    """The result of parsing a BOM source."""

    inventory: Inventory
    stats: StatsDict
    title: str | None = None
    raw_content: bytes | None = None


class BOMParserStrategy(ABC):
    """Abstract base class for all BOM parsing strategies."""

    @abstractmethod
    def can_handle(self, method: InputMethod, data: Any) -> bool:
        """Determine if this strategy can parse the given input data."""
        pass

    @staticmethod
    def _read_to_bytes(data: Any, strategy_name: str) -> bytes:
        if hasattr(data, "getvalue"):
            raw = data.getvalue()
        elif hasattr(data, "read"):
            raw = data.read()
        elif isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        elif isinstance(data, str):
            raw = data.encode("utf-8")
        else:
            raise ValueError(f"Unsupported data type for {strategy_name}: {type(data)}")
        return raw if isinstance(raw, bytes) else raw.encode("utf-8")

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
    def parse(self, data: Any, source_name: str) -> ParseResult:
        """Parse the given input data into a standard ParseResult."""
        pass
