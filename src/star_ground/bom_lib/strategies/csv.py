"""Reads spreadsheet parts lists (CSV/TSV) and the builder's stock spreadsheet."""

import csv
import io
import os
from collections.abc import Iterator
from dataclasses import dataclass

from star_ground.bom_lib.classifier import (
    is_chip_socket,
    normalize_value_by_category,
)
from star_ground.bom_lib.designators import is_designator
from star_ground.bom_lib.enums import ComponentCategory, InputMethod
from star_ground.bom_lib.errors import UnreadableSpreadsheetError
from star_ground.bom_lib.ingest import ingest_bom_line
from star_ground.bom_lib.strategies.base import BOMParserStrategy
from star_ground.bom_lib.types import (
    FORMULA_PREFIXES,
    ComponentKey,
    Inventory,
    ParseResult,
    RawBOMData,
    ReadingStats,
    SupportsName,
)
from star_ground.bom_lib.utils import expand_refs

# Column names that hold a part's designators, and its value, by preference.
_REF_COLUMNS = ("ref", "designator", "part", "location")
_VALUE_COLUMNS = ("value", "val", "description")


@dataclass(frozen=True)
class _Sheet:
    """A spreadsheet export, decoded, with how its columns are separated."""

    text: str
    dialect: type[csv.Dialect] | csv.Dialect

    def rows(self) -> Iterator[list[str]]:
        """Every row as its cells, header included.

        Raises:
            UnreadableSpreadsheetError: The file is damaged.
        """
        return _undamaged(csv.reader(io.StringIO(self.text), dialect=self.dialect))

    def records(self) -> Iterator[dict[str | None, str | None]]:
        """The rows after the first, keyed by the first row (the header).

        Raises:
            UnreadableSpreadsheetError: The file is damaged.
        """
        return _undamaged(csv.DictReader(io.StringIO(self.text), dialect=self.dialect))


def _undamaged[T](rows: Iterator[T]) -> Iterator[T]:
    """The rows, until the csv module finds the file damaged.

    It refuses a cell longer than its field size limit (131,072 characters),
    which is what an unclosed quote mark makes of the rest of the file. The
    limit is a guard on memory, so it stays.
    """
    try:
        yield from rows
    except csv.Error as e:
        raise UnreadableSpreadsheetError from e


def _read_sheet(content: bytes) -> _Sheet:
    """Decodes a spreadsheet export.

    Spreadsheets save CSV as UTF-8, or as Windows-1252 (Excel on Windows), and
    separate columns with commas, tabs (TSV) or semicolons (Excel in locales
    that write '4,7' for 4.7). Both are detected.

    Args:
        content: The file's bytes.

    Returns:
        The text and its dialect.
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252", errors="replace")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:4096], delimiters=",\t;"
        )
    except csv.Error:
        dialect = csv.excel
    return _Sheet(text, dialect)


def _has_header(first: list[str]) -> bool:
    """Whether a parts list's first row is its header.

    It is unless it names no known column and starts with a designator:
    'R1,10k' is the first part of a list with no header.
    """
    cells = [cell.lower().strip() for cell in first]
    if set(cells) & {*_REF_COLUMNS, *_VALUE_COLUMNS}:
        return True
    refs = expand_refs(first[0]) if first else []
    return not (refs and all(is_designator(ref) for ref in refs))


def parse_csv_bom(content: bytes, source_name: str) -> ParseResult:
    """Parses a CSV BOM file.

    Reads the columns its header names ('Ref', 'Value'). A list with no header
    ('R1,10k' on the first row), or a header naming neither column with only
    two columns, is read as designators in the first column and values in the
    second.

    Args:
        content: The file's bytes.
        source_name: Label for the source.

    Returns:
        A ParseResult containing updated inventory and parsing statistics.

    Raises:
        UnreadableSpreadsheetError: The file is damaged.
    """
    inventory = Inventory()
    stats: ReadingStats = ReadingStats()
    sheet = _read_sheet(content)
    first = next((row for row in sheet.rows() if row), [])

    pairs: Iterator[tuple[str | None, str | None, str]]
    if _has_header(first):
        pairs = (_header_pair(row) for row in sheet.records())
    else:
        pairs = (
            (row[0], row[1] if len(row) > 1 else None, str(row))
            for row in sheet.rows()
            if row
        )

    rows = list(pairs)
    # A list with its own chip sockets ('IC1-S, DIP-8 socket') gets none added.
    inject = not any(val and is_chip_socket(val) for _, val, _ in rows)
    for ref, val, raw in rows:
        stats.lines_read += 1
        success = False
        if ref and val:
            count = ingest_bom_line(
                inventory, source_name, ref, val, stats, inject=inject
            )
            if count > 0:
                stats.parts_found += count
                success = True

        if not success:
            stats.residuals.append(raw)

    return ParseResult(inventory=inventory, stats=stats)


def _header_pair(
    row: dict[str | None, str | None],
) -> tuple[str | None, str | None, str]:
    """A headed row's designators and value, and the row as written.

    Falls back to the first two columns when the header names neither and the
    row has only two.
    """
    row_clean = {str(k).lower().strip(): v for k, v in row.items() if k}
    ref = next((row_clean[c] for c in _REF_COLUMNS if row_clean.get(c)), None)
    val = next((row_clean[c] for c in _VALUE_COLUMNS if row_clean.get(c)), None)
    if not ref and not val and len(row_clean) == 2:
        ref, val = list(row_clean.values())
    return ref, val, str(row)


def parse_user_inventory(content: bytes) -> Inventory:
    """Parses a user's stock CSV.

    Expects columns: Category, Part, Qty.
    Applies the same value normalization as BOM parsing to ensure keys match.

    Args:
        content: The stock CSV's bytes.

    Returns:
        A populated Inventory dictionary.

    Raises:
        UnreadableSpreadsheetError: The file is damaged.
    """
    stock: Inventory = Inventory()

    for row in _read_sheet(content).records():
        # A short row has None for its missing cells.
        row_clean = {k.lower().strip(): v or "" for k, v in row.items() if k}

        cat = row_clean.get("category", "").strip()
        val = row_clean.get("part", "").strip()
        # Undo the exporter's formula guard ("'=1+1" was written for "=1+1").
        if val.startswith("'") and val[1:].startswith(FORMULA_PREFIXES):
            val = val[1:]
        qty_str = row_clean.get("qty", "0").strip()

        if cat and val:
            try:
                qty = int(qty_str)
            except ValueError:
                continue

            # Critical: Normalize value so it matches BOM keys

            try:
                cat_enum = ComponentCategory(cat)
            except ValueError:
                continue
            clean_val = normalize_value_by_category(cat_enum, val)
            key = ComponentKey(cat_enum, clean_val)

            stock.add_part("User Stock", key, ref="", qty=qty)

    return stock


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
        """Parse CSV data into a ParseResult.

        A string is never read as a file path (see `PDFParserStrategy.parse`).

        Args:
            data: An upload (`FileUpload`), file-like object, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        raw_content = self._read_to_bytes(data, "CSVParserStrategy")
        return parse_csv_bom(raw_content, source_name=source_name)
