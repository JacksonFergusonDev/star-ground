import datetime
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    import gspread


@st.cache_resource(ttl="1h")
def get_gsheet_client() -> "gspread.Client":
    """
    Establishes a persistent connection to Google Sheets.

    Uses Streamlit's resource caching to maintain a connection pool,
    refreshing every hour. Requires `gcp_service_account` to be defined
    in the Streamlit secrets.

    Returns:
        gspread.Client: An authenticated Google Sheets client.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    # Load credentials securely from Streamlit secrets
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(creds)


def save_feedback(rating: str, text: str) -> None:
    """
    Appends a new feedback entry to the "Star Ground Feedback" Google Sheet.

    Args:
        rating (str): The user's rating (e.g., "🤩", "😕").
        text (str): The user's comment or bug report.

    Raises:
        Exception: If connection fails or sheet is not found.
    """
    client = get_gsheet_client()
    sheet = client.open("Star Ground Feedback").sheet1

    # Append timestamp, rating, and comment as a new row
    row = [str(datetime.datetime.now()), rating, text]
    sheet.append_row(row)
