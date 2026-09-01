"""Unit tests for feedback submission and Google Sheets integration."""

from unittest.mock import MagicMock, patch

import pytest

from src.bom_lib.enums import FeedbackRating
from src.feedback import save_feedback


class TestFeedbackSubmission:
    """Tests for save_feedback function."""

    @pytest.fixture
    def mock_sheet(self) -> MagicMock:
        sheet = MagicMock()
        client = MagicMock()
        client.open.return_value.sheet1 = sheet
        return client

    def test_save_feedback_appends_row_with_enum_value(
        self, mock_sheet: MagicMock
    ) -> None:
        with patch("src.feedback.get_gsheet_client", return_value=mock_sheet):
            save_feedback(FeedbackRating.EXCELLENT, "Awesome tool!")

        sheet1 = mock_sheet.open.return_value.sheet1
        sheet1.append_row.assert_called_once()
        appended_row = sheet1.append_row.call_args[0][0]

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
        self, mock_sheet: MagicMock, rating: FeedbackRating, expected_emoji: str
    ) -> None:
        with patch("src.feedback.get_gsheet_client", return_value=mock_sheet):
            save_feedback(rating, "Testing rating")

        sheet1 = mock_sheet.open.return_value.sheet1
        appended_row = sheet1.append_row.call_args[0][0]
        assert appended_row[1] == expected_emoji

    def test_save_feedback_raises_on_sheet_error(self, mock_sheet: MagicMock) -> None:
        mock_sheet.open.side_effect = RuntimeError(
            "Google Sheets API connection failed"
        )

        with (
            patch("src.feedback.get_gsheet_client", return_value=mock_sheet),
            pytest.raises(RuntimeError, match="Google Sheets API connection failed"),
        ):
            save_feedback(FeedbackRating.BAD, "Bug report")
