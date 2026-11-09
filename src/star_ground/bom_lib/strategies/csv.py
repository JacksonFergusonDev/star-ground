"""Reads spreadsheet parts lists (CSV/TSV) and the builder's stock spreadsheet."""

import csv
import io
import os

from star_ground.bom_lib.classifier import (
    normalize_value_by_category,
)
from star_ground.bom_lib.enums import ComponentCategory, InputMethod
from star_ground.bom_lib.ingest import ingest_bom_line
from star_ground.bom_lib.strategies.base import BOMParserStrategy
from star_ground.bom_lib.types import (
    FORMULA_PREFIXES,
    Inventory,
    ParseResult,
    RawBOMData,
    ReadingStats,
    SupportsName,
    make_component_key,
)


def _read_table(filepath: str) -> csv.DictReader[str]:
    """Reads a spreadsheet export as rows keyed by its header.

    Spreadsheets save CSV as UTF-8, or as Windows-1252 (Excel on Windows), and
    separate columns with commas, tabs (TSV) or semicolons (Excel in locales
    that write '4,7' for 4.7). Both are detected.

    Args:
        filepath: Path to the file.

    Returns:
        A reader over the file's rows.
    """
    with open(filepath, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:4096], delimiters=",\t;"
        )
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(text), dialect=dialect)


def parse_csv_bom(filepath: str, source_name: str) -> ParseResult:
    """Parses a CSV BOM file.

    Attempts to intelligently guess columns ('Ref', 'Value') or falls back
    to using the first two columns.

    Args:
        filepath: Path to the CSV file.
        source_name: Label for the source.

    Returns:
        A ParseResult containing updated inventory and parsing statistics.
    """
    inventory = Inventory()
    stats: ReadingStats = ReadingStats()

    for row in _read_table(filepath):
        stats.lines_read += 1
        row_clean = {str(k).lower().strip(): v for k, v in row.items() if k}

        # Try explicit columns
        ref = (
            row_clean.get("ref")
            or row_clean.get("designator")
            or row_clean.get("part")
            or row_clean.get("location")
        )
        val = (
            row_clean.get("value")
            or row_clean.get("val")
            or row_clean.get("description")
        )

        # Fallback: Assume Col 1 = Ref, Col 2 = Val
        if not ref and not val and len(row_clean) == 2:
            vals = list(row_clean.values())
            ref = vals[0]
            val = vals[1]

        success = False
        if ref and val:
            count = ingest_bom_line(inventory, source_name, ref, val, stats)
            if count > 0:
                stats.parts_found += count
                success = True

        if not success:
            stats.residuals.append(str(row))

    return ParseResult(inventory=inventory, stats=stats)


def parse_user_inventory(filepath: str) -> Inventory:
    """Parses a user's stock CSV.

    Expects columns: Category, Part, Qty.
    Applies the same value normalization as BOM parsing to ensure keys match.

    Args:
        filepath: Path to the user inventory CSV.

    Returns:
        A populated Inventory dictionary.
    """
    stock: Inventory = Inventory()

    for row in _read_table(filepath):
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
            key = make_component_key(cat_enum, clean_val)

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
        """Parse CSV data into a ParseResult, managing temporary files as needed.

        A string is never read as a file path (see `PDFParserStrategy.parse`).

        Args:
            data: An upload (`FileUpload`), file-like object, or raw bytes.
            source_name: Display label for the source.

        Returns:
            ParseResult containing the parsed inventory and statistics.
        """
        ext = ".csv"
        if isinstance(data, SupportsName):
            filename = str(data.name)
            detected_ext = os.path.splitext(filename)[1]
            if detected_ext:
                ext = detected_ext.lower()

        raw_content = self._read_to_bytes(data, "CSVParserStrategy")
        with self._temp_file_from_bytes(raw_content, ext) as tmp_path:
            return parse_csv_bom(tmp_path, source_name=source_name)
