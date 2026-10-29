"""Unit tests for feedback submission and Google Sheets integration."""

from unittest.mock import MagicMock, patch

import pytest

from src.bom_lib.enums import FeedbackRating
from src.bom_lib.types import Location
from src.feedback import (
    FEEDBACK_SHEET,
    PartReport,
    connect_feedback_sheet,
    save_feedback,
    save_part_report,
    save_report,
)


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


def test_a_report_keeps_the_end_of_a_long_traceback() -> None:
    """A traceback ends with the exception: that's what a trim must keep."""
    sheet = MagicMock()
    detail = "frame\n" * 20_000 + "KeyError: 'toks'"

    save_report(sheet, "A1B2C3", "Fuzz.pdf", "", detail)

    kept = sheet.append_row.call_args.args[0][4]
    assert kept.endswith("KeyError: 'toks'")
    assert len(kept) < 50_000


def test_a_report_without_its_traceback_points_to_the_log() -> None:
    sheet = MagicMock()

    save_report(sheet, "A1B2C3", "Fuzz.pdf", "", "")

    assert "Bug A1B2C3" in sheet.append_row.call_args.args[0][4]


def test_a_part_report_numbers_pages_as_a_viewer_does() -> None:
    sheet = MagicMock()
    report = PartReport(
        document="https://docs.pedalpcb.com/project/Kliche-PedalPCB.pdf",
        ref="R1",
        read="1k",
        correction="10k",
        location=Location(page=1, x0=40.2, top=100, x1=98, bottom=110.6),
        comment="",
    )

    save_part_report(sheet, report)

    row = sheet.append_row.call_args.args[0]
    assert row[1:] == [report.document, "R1", "1k", "10k", 2, "40, 100, 98, 111", ""]


def test_a_part_report_without_a_correction_or_place() -> None:
    sheet = MagicMock()
    report = PartReport("Fuzz.pdf", "R9", "", None, None, "R9 is missing")

    save_part_report(sheet, report)

    assert sheet.append_row.call_args.args[0][4:] == ["", "", "", "R9 is missing"]
