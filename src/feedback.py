import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.bom_lib.enums import FeedbackRating

if TYPE_CHECKING:
    import gspread

FEEDBACK_SHEET = "Star Ground Feedback"
REPORTS_TAB = "Reports"
_REPORT_HEADERS = ["Time", "Code", "Document", "Comment", "Detail"]
# A Google Sheets cell holds at most 50,000 characters.
_MAX_CELL = 45_000


def _spreadsheet(credentials: Mapping[str, Any]) -> gspread.Spreadsheet:
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
        dict(credentials), scopes=scope
    )
    return gspread.authorize(creds).open(FEEDBACK_SHEET)


def connect_feedback_sheet(credentials: Mapping[str, Any]) -> gspread.Worksheet:
    """Opens the Google Sheet that in-app feedback is appended to.

    The frontend supplies the credentials (Streamlit reads them from its
    secrets) and decides how long to reuse the connection.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        gspread.Worksheet: The first worksheet of the feedback sheet.
    """
    return _spreadsheet(credentials).sheet1


def connect_report_sheet(credentials: Mapping[str, Any]) -> gspread.Worksheet:
    """Opens the feedback sheet's Reports tab, adding it the first time.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        gspread.Worksheet: The tab bug reports are appended to.
    """
    import gspread

    spreadsheet = _spreadsheet(credentials)
    try:
        return spreadsheet.worksheet(REPORTS_TAB)
    except gspread.WorksheetNotFound:
        tab = spreadsheet.add_worksheet(REPORTS_TAB, rows=1, cols=len(_REPORT_HEADERS))
        tab.append_row(_REPORT_HEADERS)
        return tab


def save_feedback(sheet: gspread.Worksheet, rating: FeedbackRating, text: str) -> None:
    """Appends a feedback entry to the feedback sheet.

    Args:
        sheet: The sheet from `connect_feedback_sheet`.
        rating: The user's FeedbackRating enum (e.g., FeedbackRating.EXCELLENT).
        text: The user's comment or bug report.

    Raises:
        Exception: If the sheet cannot be written.
    """
    # Append timestamp, rating value, and comment as a new row
    row = [str(datetime.datetime.now()), rating.value, text]
    sheet.append_row(row)


def save_report(
    sheet: gspread.Worksheet, code: str, document: str, comment: str, detail: str
) -> None:
    """Appends a bug report to the Reports tab.

    Args:
        sheet: The tab from `connect_report_sheet`.
        code: The bug's code, as in the server's log.
        document: What was being read: an upload's name or a link.
        comment: Anything the builder added.
        detail: Star Ground's version, what it was doing, and the traceback;
            empty when this server no longer holds the bug.

    Raises:
        Exception: If the sheet cannot be written.
    """
    # A traceback ends with the exception, so a long one keeps its end.
    kept = detail[-_MAX_CELL:] if detail else f"Search the log for 'Bug {code}'."
    row = [str(datetime.datetime.now()), code, document, comment, kept]
    sheet.append_row(row)
