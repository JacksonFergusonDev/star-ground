"""Appends in-app feedback, bug reports and part reports to a Google Sheet.

It speaks to Google's Sheets and Drive APIs directly, through google-auth's
authorized session: the sheet is found by its name, each tab is opened by its
title (and added the first time), and every report is one appended row.
"""

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from star_ground.bom_lib.enums import FeedbackRating
from star_ground.bom_lib.types import Location

FEEDBACK_SHEET = "Star Ground Feedback"
REPORTS_TAB = "Reports"
_REPORT_HEADERS = ["Time", "Code", "Document", "Comment", "Detail"]
PARTS_TAB = "Parts"
_PART_HEADERS = [
    "Time",
    "Document",
    "Ref",
    "Read",
    "Correction",
    "Page",
    "Box",
    "Comment",
]
# A Google Sheets cell holds at most 50,000 characters.
_MAX_CELL = 45_000

_SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
_DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    # Only to find the sheet by its name.
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
_TIMEOUT = 20


class SheetNotFoundError(LookupError):
    """The service account can't see a sheet with the feedback sheet's name."""


@dataclass(frozen=True, slots=True)
class SheetTab:
    """One tab of a Google Sheet that rows are appended to.

    Attributes:
        session: An authorized session for the service account.
        spreadsheet: The sheet's ID.
        title: The tab's title.
    """

    session: Any
    spreadsheet: str
    title: str

    def append_row(self, row: Sequence[object]) -> None:
        """Appends one row after the tab's last.

        Args:
            row: The cells, written as given (never read as formulas).

        Raises:
            requests.HTTPError: If Google refuses the write.
        """
        cells = quote(f"'{self.title}'!A1", safe="")
        response = self.session.post(
            f"{_SHEETS}/{self.spreadsheet}/values/{cells}:append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": [list(row)]},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()


def _session(credentials: Mapping[str, Any]) -> Any:
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
        dict(credentials), scopes=_SCOPES
    )
    return AuthorizedSession(creds)  # type: ignore[no-untyped-call]


def _spreadsheet_id(session: Any) -> str:
    """The feedback sheet's ID, found by its name among the files it can see."""
    response = session.get(
        _DRIVE_FILES,
        params={
            "q": (
                f"name = '{FEEDBACK_SHEET}' and trashed = false and "
                "mimeType = 'application/vnd.google-apps.spreadsheet'"
            ),
            "fields": "files(id)",
            "pageSize": 1,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    files = response.json().get("files", [])
    if not files:
        raise SheetNotFoundError(FEEDBACK_SHEET)
    return str(files[0]["id"])


def _titles(session: Any, spreadsheet: str) -> list[str]:
    """The sheet's tab titles, in order."""
    response = session.get(
        f"{_SHEETS}/{spreadsheet}",
        params={"fields": "sheets.properties.title"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return [s["properties"]["title"] for s in response.json().get("sheets", [])]


def connect_feedback_sheet(credentials: Mapping[str, Any]) -> SheetTab:
    """Opens the first tab of the sheet that in-app feedback is appended to.

    The server supplies the credentials (from its secret) and decides how
    long to reuse the connection.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        The feedback sheet's first tab.
    """
    session = _session(credentials)
    spreadsheet = _spreadsheet_id(session)
    return SheetTab(session, spreadsheet, _titles(session, spreadsheet)[0])


def _tab(credentials: Mapping[str, Any], name: str, headers: Sequence[str]) -> SheetTab:
    session = _session(credentials)
    spreadsheet = _spreadsheet_id(session)
    tab = SheetTab(session, spreadsheet, name)
    if name not in _titles(session, spreadsheet):
        grid = {"rowCount": 1, "columnCount": len(headers)}
        response = session.post(
            f"{_SHEETS}/{spreadsheet}:batchUpdate",
            json={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {"title": name, "gridProperties": grid}
                        }
                    }
                ]
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        tab.append_row(headers)
    return tab


def connect_report_sheet(credentials: Mapping[str, Any]) -> SheetTab:
    """Opens the feedback sheet's Reports tab, adding it the first time.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        The tab bug reports are appended to.
    """
    return _tab(credentials, REPORTS_TAB, _REPORT_HEADERS)


def connect_part_sheet(credentials: Mapping[str, Any]) -> SheetTab:
    """Opens the feedback sheet's Parts tab, adding it the first time.

    Args:
        credentials: A Google service account's key, as its JSON fields.

    Returns:
        The tab part reports are appended to.
    """
    return _tab(credentials, PARTS_TAB, _PART_HEADERS)


@dataclass(frozen=True, slots=True)
class PartReport:
    """A builder's report that a part was read wrong.

    Attributes:
        document: The link, or an upload's name and content hash; never the
            document itself.
        ref: The designator.
        read: The value as Star Ground read it.
        correction: The value the document prints, if the builder gave one.
        location: Where the part was read.
        comment: Anything the builder added.
    """

    document: str
    ref: str
    read: str
    correction: str | None
    location: Location | None
    comment: str


def save_feedback(sheet: SheetTab, rating: FeedbackRating, text: str) -> None:
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
    sheet: SheetTab, code: str, document: str, comment: str, detail: str
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


def save_part_report(sheet: SheetTab, report: PartReport) -> None:
    """Appends a part report to the Parts tab.

    Pages are numbered from 1, as a PDF viewer numbers them; the box is in
    points from the page's top-left.

    Args:
        sheet: The tab from `connect_part_sheet`.
        report: The report.

    Raises:
        Exception: If the sheet cannot be written.
    """
    at = report.location
    row = [
        str(datetime.datetime.now()),
        report.document,
        report.ref,
        report.read,
        report.correction or "",
        at.page + 1 if at else "",
        f"{at.x0:.0f}, {at.top:.0f}, {at.x1:.0f}, {at.bottom:.0f}" if at else "",
        report.comment,
    ]
    sheet.append_row(row)
