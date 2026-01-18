import datetime
import streamlit as st


@st.cache_resource(ttl="1h")
def get_gsheet_client():
    """Establishes a persistent connection to Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(creds)


def save_feedback(rating, text):
    """Appends feedback row using the cached client."""
    client = get_gsheet_client()
    sheet = client.open("Star Ground Feedback").sheet1

    # Append timestamp, rating, and comment
    row = [str(datetime.datetime.now()), rating, text]
    sheet.append_row(row)
