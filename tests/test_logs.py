"""Logs written as Cloud Logging and Error Reporting read them."""

import json
import logging
import sys

import pytest

from star_ground.bom_lib.errors import record_bug
from star_ground.server.logs import CloudFormatter, configure_logging


def _entry(record: logging.LogRecord) -> dict[str, object]:
    line = CloudFormatter().format(record)
    assert "\n" not in line  # one entry per line, traceback and all
    entry: dict[str, object] = json.loads(line)
    return entry


def _record(level: int, message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "star_ground.test", level, __file__, 1, message, (), None
    )
    record.__dict__.update(extra)
    return record


def test_a_bug_becomes_an_error_reporting_event() -> None:
    try:
        raise KeyError("toks")
    except KeyError:
        record = _record(
            logging.ERROR, "Bug A1B2C3 in Star Ground x", bug_code="A1B2C3"
        )
        record.exc_info = sys.exc_info()

    entry = _entry(record)

    assert entry["severity"] == "ERROR"
    assert str(entry["@type"]).endswith("ReportedErrorEvent")
    context = entry["serviceContext"]
    assert isinstance(context, dict)
    assert context["service"] == "star-ground"
    assert context["version"]
    message = str(entry["message"])
    assert message.startswith("Bug A1B2C3")
    assert "Traceback (most recent call last)" in message
    assert message.endswith("KeyError: 'toks'")
    assert entry["logging.googleapis.com/labels"] == {"bug_code": "A1B2C3"}


def test_a_builder_s_report_is_kept_but_isn_t_an_error() -> None:
    report = {"document": "Fuzz.pdf", "comment": "Added three at once."}
    entry = _entry(
        _record(
            logging.WARNING, "Report of bug A1B2C3", bug_code="A1B2C3", report=report
        )
    )

    assert entry["severity"] == "WARNING"
    assert "@type" not in entry
    assert entry["report"] == report
    assert entry["logging.googleapis.com/labels"] == {"bug_code": "A1B2C3"}


def test_record_bug_logs_its_code_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        raise ZeroDivisionError
    except ZeroDivisionError:
        bug = record_bug("reading Fuzz.pdf")

    (record,) = [r for r in caplog.records if getattr(r, "bug_code", None) == bug.code]
    entry = _entry(record)
    assert "reading Fuzz.pdf" in str(entry["message"])
    assert "ZeroDivisionError" in str(entry["message"])


def test_logs_stay_plain_off_cloud_run() -> None:
    root = logging.getLogger()
    before = list(root.handlers)

    configure_logging({})

    assert root.handlers == before


def test_on_cloud_run_logs_are_json() -> None:
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        configure_logging({"K_SERVICE": "star-ground"})
        (handler,) = root.handlers
        assert isinstance(handler.formatter, CloudFormatter)
    finally:
        root.handlers[:] = before
        root.setLevel(level)
        logging.getLogger("star_ground").setLevel(logging.NOTSET)
