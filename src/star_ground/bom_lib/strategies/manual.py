"""Reads pasted or uploaded "R1 10k" parts lists, one part per line."""

import re

from star_ground.bom_lib.enums import InputMethod
from star_ground.bom_lib.ingest import ingest_bom_line, record_pcb
from star_ground.bom_lib.strategies.base import BOMParserStrategy
from star_ground.bom_lib.types import (
    FileUpload,
    Inventory,
    ParseResult,
    RawBOMData,
    ReadingStats,
)


def parse_with_verification(
    bom_list: list[str], source_name: str = "Manual Input"
) -> ParseResult:
    """Parses a list of raw text strings (Manual BOM Input).

    Handles standard formats like "R1 10k" and special cases like "PCB Name".

    Args:
        bom_list: List of strings (lines from a text area).
        source_name: Label for the source of these parts.

    Returns:
        A ParseResult containing updated inventory and parsing statistics.
    """
    inventory = Inventory()
    stats: ReadingStats = ReadingStats()

    # Regex: Matches Ref + Separator + Value.
    pattern = re.compile(r"^([a-zA-Z0-9_\-]+)[\s,]+(\S.*?)\s*$")

    for raw_text in bom_list:
        lines = raw_text.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue
            stats.lines_read += 1

            # Check for PCB Definition (Prefix "PCB" + Value)
            parts = line.split(None, 1)
            if parts and parts[0].upper() == "PCB":
                # Skip header "PCB"
                if len(parts) == 1:
                    continue

                clean_name = parts[1].strip()
                record_pcb(inventory, source_name, clean_name, stats)
                continue

            match = pattern.match(line)
            success = False

            if match:
                ref_raw = match.group(1).upper()
                val_raw = match.group(2)
                count = ingest_bom_line(inventory, source_name, ref_raw, val_raw, stats)
                if count > 0:
                    stats.parts_found += count
                    success = True

            if not success:
                # Fallback: Check if line contains "PCB" (heuristic)
                if "PCB" in line.upper() and line.strip().upper() != "PCB":
                    record_pcb(inventory, source_name, line.strip(), stats)
                else:
                    stats.residuals.append(line)

    return ParseResult(inventory=inventory, stats=stats)


TEXT_SUFFIX = ".txt"


def _is_text_upload(data: RawBOMData) -> bool:
    return isinstance(data, FileUpload) and data.name.lower().endswith(TEXT_SUFFIX)


class ManualInputStrategy(BOMParserStrategy):
    """Parses raw text BOM lines pasted in."""

    def can_handle(self, method: InputMethod, data: RawBOMData) -> bool:
        """Determine if this strategy can handle manual text input."""
        if method == InputMethod.UPLOAD_FILE:
            return _is_text_upload(data)
        if method in (InputMethod.PASTE_TEXT, InputMethod.FROM_URL):
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
