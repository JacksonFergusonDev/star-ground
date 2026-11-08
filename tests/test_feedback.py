"""Unit tests for feedback submission and Google Sheets integration."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from star_ground.bom_lib.enums import FeedbackRating
from star_ground.bom_lib.types import Location
from star_ground.feedback import (
    FEEDBACK_SHEET,
    PartReport,
    SheetNotFoundError,
    connect_feedback_sheet,
    connect_part_sheet,
    connect_report_sheet,
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


class _Google:
    """A stand-in for the Sheets and Drive APIs, recording each call."""

    def __init__(self, tabs: list[str]) -> None:
        self.tabs = tabs
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _answer(self, body: dict[str, Any]) -> MagicMock:
        response = MagicMock()
        response.json.return_value = body
        return response

    def get(self, url: str, **kwargs: Any) -> MagicMock:
        self.calls.append(("GET", url, kwargs))
        if "drive" in url:
            return self._answer({"files": [{"id": "SHEET"}]})
        return self._answer(
            {"sheets": [{"properties": {"title": t}} for t in self.tabs]}
        )

    def post(self, url: str, **kwargs: Any) -> MagicMock:
        self.calls.append(("POST", url, kwargs))
        return self._answer({})


def _connected(google: _Google) -> Any:
    return patch("star_ground.feedback._session", return_value=google)


def test_the_feedback_sheet_is_found_by_name_and_its_first_tab_used() -> None:
    google = _Google(["Ratings", "Reports"])
    with _connected(google):
        tab = connect_feedback_sheet({"client_email": "bot@example.test"})

    _, url, kwargs = google.calls[0]
    assert "drive/v3/files" in url
    assert f"name = '{FEEDBACK_SHEET}'" in kwargs["params"]["q"]
    assert (tab.spreadsheet, tab.title) == ("SHEET", "Ratings")


def test_a_row_is_appended_as_written() -> None:
    google = _Google(["Ratings"])
    with _connected(google):
        tab = connect_feedback_sheet({})
    save_feedback(tab, FeedbackRating.GOOD, "=1+1")

    method, url, kwargs = google.calls[-1]
    assert method == "POST"
    assert url.endswith("/SHEET/values/%27Ratings%27%21A1:append")
    # RAW: a comment is never read as a formula.
    assert kwargs["params"]["valueInputOption"] == "RAW"
    assert kwargs["json"]["values"][0][1:] == [FeedbackRating.GOOD.value, "=1+1"]


def test_a_missing_tab_is_added_with_its_headers() -> None:
    google = _Google(["Ratings"])
    with _connected(google):
        tab = connect_report_sheet({})

    posts = [(url, kwargs) for method, url, kwargs in google.calls if method == "POST"]
    (add, _), (append, headers) = posts
    assert add.endswith(":batchUpdate")
    assert append.endswith("/values/%27Reports%27%21A1:append")
    assert headers["json"]["values"] == [
        ["Time", "Code", "Document", "Comment", "Detail"]
    ]
    assert tab.title == "Reports"


def test_an_existing_tab_is_left_alone() -> None:
    google = _Google(["Ratings", "Parts"])
    with _connected(google):
        connect_part_sheet({})

    assert not [c for c in google.calls if c[0] == "POST"]


def test_a_sheet_the_account_cannot_see_says_so() -> None:
    google = _Google([])
    google.get = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(json=MagicMock(return_value={"files": []}))
    )
    with _connected(google), pytest.raises(SheetNotFoundError):
        connect_feedback_sheet({})


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
