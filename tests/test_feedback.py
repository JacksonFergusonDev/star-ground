"""Unit tests for feedback submission and Google Sheets integration."""

from unittest.mock import MagicMock, patch

import pytest

from src.bom_lib.enums import FeedbackRating
from src.feedback import FEEDBACK_SHEET, connect_feedback_sheet, save_feedback


class TestFeedbackSubmission:
    """Tests for save_feedback function."""

    def test_save_feedback_appends_row_with_enum_value(self) -> None:
        sheet = MagicMock()
        save_feedback(sheet, FeedbackRating.EXCELLENT, "Awesome tool!")

        sheet.append_row.assert_called_once()
        appended_row = sheet.append_row.call_args[0][0]

        assert len(appended_row) == 3
        # First item is timestamp string
        assert isinstance(appended_row[0], str)
        # Second item is enum string value "🤩"
        assert appended_row[1] == FeedbackRating.EXCELLENT.value
        assert appended_row[1] == "🤩"
        # Third item is text comment
        assert appended_row[2] == "Awesome tool!"

    @pytest.mark.parametrize(
        ("rating", "expected_emoji"),
        [
            (FeedbackRating.TERRIBLE, "😡"),
            (FeedbackRating.BAD, "😕"),
            (FeedbackRating.NEUTRAL, "😐"),
            (FeedbackRating.GOOD, "🙂"),
            (FeedbackRating.EXCELLENT, "🤩"),
        ],
    )
    def test_save_feedback_all_ratings(
        self, rating: FeedbackRating, expected_emoji: str
    ) -> None:
        sheet = MagicMock()
        save_feedback(sheet, rating, "Testing rating")

        appended_row = sheet.append_row.call_args[0][0]
        assert appended_row[1] == expected_emoji

    def test_save_feedback_raises_on_sheet_error(self) -> None:
        sheet = MagicMock()
        sheet.append_row.side_effect = RuntimeError(
            "Google Sheets API connection failed"
        )

        with pytest.raises(RuntimeError, match="Google Sheets API connection failed"):
            save_feedback(sheet, FeedbackRating.BAD, "Bug report")


def test_connect_opens_the_feedback_sheet_with_the_given_credentials() -> None:
    credentials = {"client_email": "bot@example.test"}
    with (
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_info"
        ) as from_info,
        patch("gspread.authorize") as authorize,
    ):
        sheet = connect_feedback_sheet(credentials)

    assert from_info.call_args[0][0] == credentials
    authorize.return_value.open.assert_called_once_with(FEEDBACK_SHEET)
    assert sheet is authorize.return_value.open.return_value.sheet1
