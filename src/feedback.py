import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from src.bom_lib.enums import FeedbackRating

if TYPE_CHECKING:
    import gspread

FEEDBACK_SHEET = "Star Ground Feedback"


def connect_feedback_sheet(credentials: Mapping[str, Any]) -> gspread.Worksheet:
    """Opens the Google Sheet that in-app feedback is appended to.

    The frontend supplies the credentials (Streamlit reads them from its
    secrets) and decides how long to reuse the connection.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        gspread.Worksheet: The first worksheet of the feedback sheet.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
        dict(credentials), scopes=scope
    )
    return gspread.authorize(creds).open(FEEDBACK_SHEET).sheet1


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
