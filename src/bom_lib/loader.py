"""
Input handling and parsing orchestration.

This module abstracts the source of the BOM data (File, URL, Text)
from the logic used to parse it. It handles HTTP requests, temporary files,
and parser dispatching.
"""

import logging
import os
import tempfile
from typing import Any

import requests

from src.bom_lib.parser import (
    parse_csv_bom,
    parse_pedalpcb_pdf,
    parse_with_verification,
)
from src.bom_lib.types import InventoryType, StatsDict, create_empty_inventory

logger = logging.getLogger(__name__)


def _process_pdf_content(
    content: bytes, source_name: str
) -> tuple[InventoryType, StatsDict]:
    """Helper to handle binary PDF content via temp file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return parse_pedalpcb_pdf(tmp_path, source_name=source_name)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def process_input_data(
    method: str, data: Any, source_name: str
) -> tuple[InventoryType, StatsDict, str | None, bytes | None]:
    """
    Unified handler for processing Text, File, and URL inputs.

    Args:
        method: The input method ("Paste Text", "Preset", "From URL", "Upload File").
        data: The raw data associated with the method (String, UploadedFile, etc.).
        source_name: A display name for logging and error messages.

    Returns:
        A tuple containing:
            - InventoryType: The parsed inventory structure.
            - StatsDict: Parsing statistics.
            - str | None: The detected project title (if available).
            - bytes | None: The raw binary content (if a file/URL was processed).
    """
    # 1. Handle empty data case
    if not data:
        return (
            create_empty_inventory(),
            {
                "lines_read": 0,
                "parts_found": 0,
                "residuals": [],
                "extracted_title": None,
                "seen_refs": set(),
                "errors": [],
            },
            None,
            None,
        )

    # 2. Dispatch based on method
    try:
        # A. PASTE TEXT / PRESET
        if method in ["Paste Text", "Preset"]:
            inv, stats = parse_with_verification([str(data)], source_name=source_name)
            return inv, stats, None, None

        # B. URL
        elif method == "From URL":
            url = str(data).strip()
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Detection
            is_pdf = url.lower().endswith(".pdf") or response.content.startswith(
                b"%PDF"
            )

            if is_pdf:
                inv, stats = _process_pdf_content(response.content, source_name)
                # Return content bytes so the UI can cache them
                return inv, stats, stats.get("extracted_title"), response.content
            else:
                inv, stats = parse_with_verification(
                    [response.text], source_name=source_name
                )
                return inv, stats, None, None

        # C. UPLOAD FILE
        elif method == "Upload File":
            # data is expected to be a file-like object (Streamlit UploadedFile)
            if hasattr(data, "name"):
                filename = data.name
                content = data.getvalue()
            else:
                raise ValueError("Invalid file object provided.")

            ext = os.path.splitext(filename)[1].lower()

            if ext == ".pdf":
                inv, stats = _process_pdf_content(content, source_name)
                return inv, stats, stats.get("extracted_title"), content
            else:
                # CSV / Text handling via temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    inv, stats = parse_csv_bom(tmp_path, source_name=source_name)
                    return inv, stats, None, None
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

    except Exception as e:
        logger.error(f"Error processing {source_name}: {e}")
        return (
            create_empty_inventory(),
            {
                "lines_read": 0,
                "parts_found": 0,
                "residuals": [],
                "extracted_title": None,
                "seen_refs": set(),
                "errors": [str(e)],
            },
            None,
            None,
        )

    return (
        create_empty_inventory(),
        {
            "lines_read": 0,
            "parts_found": 0,
            "residuals": [],
            "extracted_title": None,
            "seen_refs": set(),
            "errors": ["Unknown Method"],
        },
        None,
        None,
    )
