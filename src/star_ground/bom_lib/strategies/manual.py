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

# A designator, separators, then its value. The line is already stripped, so
# the value runs to the end: a lazy value followed by `\s*$` would retry the
# trailing match at every character, quadratic in a long run of spaces.
_LISTING = re.compile(r"([a-zA-Z0-9_\-]+)[\s,]+(\S.*)")
_SEPARATOR = re.compile(r"[\s,]*")
# A designator's letters: the 'C' of 'C1-C3'.
_PREFIX = re.compile(r"[A-Za-z]+")
# The next item of a designator list, after a comma: 'R2', 'R5-R7', '5-7'.
_NEXT_ITEM = re.compile(r"\s*,\s*([a-zA-Z0-9_\-]+)(?![^\s,])")
# An item that continues a list: a designator ('R2', 'R5-R7') or a bare
# number or range reusing the list's letters ('C1-C3, 5-7', 'C12, 15, 16').
_CONTINUATION = re.compile(r"([A-Za-z]*)\d+[A-Ha-h]?(?:-([A-Za-z]*)\d+[A-Ha-h]?)?")


def _split_listing(line: str) -> tuple[str, str] | None:
    """Splits a pasted line into its designators and its value.

    A comma-separated list of designators stays together ('R1, R2, R5 10k',
    'C1-C3, 5-7 100n'). An item continues the list only if it is a designator
    of the same kind or a bare number, and something is left for the value.
    A bare number is only read as a designator when a space, not a comma,
    comes before the value: in 'R1, 100, 1/4W' the 100 is R1's value.

    Args:
        line: One stripped line.

    Returns:
        The designators and the value, or None if the line is no listing.
    """
    match = _LISTING.fullmatch(line)
    if match is None:
        return None
    first = match.group(1)
    prefix = _PREFIX.match(first)
    refs, end = [first], match.end(1)
    # Where the list stood when it last ended with a designator, not a number.
    named = (1, end)
    while prefix is not None and (item := _NEXT_ITEM.match(line, end)):
        token = item.group(1)
        shape = _CONTINUATION.fullmatch(token)
        letters = prefix.group(0).upper()
        if (
            shape is None
            or shape.group(1).upper() not in ("", letters)
            or (shape.group(2) or "").upper() not in ("", letters)
            or _after_separator(line, item.end()) == len(line)
        ):
            break
        refs.append(token)
        end = item.end()
        if shape.group(1):
            named = (len(refs), end)
    if len(refs) > named[0] and "," in line[end : _after_separator(line, end)]:
        # 'R1, 100, 1/4W': the trailing numbers are the value.
        count, end = named
        refs = refs[:count]
    return ",".join(refs), line[_after_separator(line, end) :]


def _after_separator(line: str, pos: int) -> int:
    """Where the separators (spaces and commas) starting at `pos` end."""
    separator = _SEPARATOR.match(line, pos)
    return separator.end() if separator else pos


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

            listing = _split_listing(line)
            success = False

            if listing:
                ref_raw, val_raw = listing[0].upper(), listing[1]
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
