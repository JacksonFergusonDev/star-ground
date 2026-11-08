"""Logs as Google Cloud reads them: one JSON object per line.

On Cloud Run every line a container prints is its own log entry, so a plain
traceback arrives in pieces. Written as JSON, an entry keeps its traceback,
its severity and its labels. A bug's entry is also marked as an error event,
which Error Reporting groups by cause, counts and can alert on.

Records carry Star Ground's own fields as `extra`:

- `bug_code`: the code the builder saw, as a label to search by
  (`labels.bug_code="A1B2C3"`).
- `report`: what a builder sent with a report, kept in the entry.
"""

import json
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any

from star_ground import version

_ERROR_EVENT = (
    "type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent"
)
_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class CloudFormatter(logging.Formatter):
    """Formats a record as one line of JSON for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        """The record as a structured log entry.

        An error logged with its exception becomes an Error Reporting event:
        its message ends with the traceback, and it names the service and
        release it happened in.
        """
        message = record.getMessage()
        entry: dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "logger": record.name,
        }
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
            if record.levelno >= logging.ERROR:
                entry["@type"] = _ERROR_EVENT
                entry["serviceContext"] = {
                    "service": "star-ground",
                    "version": version(),
                }
        entry["message"] = message
        bug_code = getattr(record, "bug_code", None)
        if bug_code:
            entry["logging.googleapis.com/labels"] = {"bug_code": bug_code}
        report = getattr(record, "report", None)
        if report:
            entry["report"] = report
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(env: Mapping[str, str] = os.environ) -> None:
    """Writes Star Ground's logs as JSON when running on Cloud Run.

    Cloud Run sets `K_SERVICE`. Anywhere else (a laptop, a self-hosted
    container) logs stay as Python prints them.

    Args:
        env: The environment.
    """
    if "K_SERVICE" not in env:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    # Libraries speak up only when something's wrong; Star Ground says more.
    root.setLevel(logging.WARNING)
    logging.getLogger("star_ground").setLevel(logging.INFO)
