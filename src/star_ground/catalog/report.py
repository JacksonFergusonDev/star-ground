"""What the catalog job tells whoever is watching it, in two forms.

Nothing the job decides is silent: every document ends with an `Outcome` and
a reason, and the summary lists every one that wasn't added.

- **In GitHub Actions** (`GITHUB_ACTIONS=true`): each publisher is a
  collapsible `::group::`, failures and bot checks are `::warning::` and
  `::error::` annotations on the run, and the summary is a Markdown table in
  the run's summary page (`GITHUB_STEP_SUMMARY`).
- **Anywhere else:** Rich output, with a rule per publisher, a coloured label
  per document and tables at the end.
"""

import logging
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from star_ground.cli_output import CliOutput, in_github_actions

if TYPE_CHECKING:
    from rich.console import Console


class Outcome(StrEnum):
    """What became of one document."""

    LISTED = "listed"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    NOT_CLEAN = "not clean"
    FAILED = "failed"
    BLOCKED = "blocked"
    DISALLOWED = "disallowed"
    POSTPONED = "postponed"
    REMOVED = "removed"
    GONE = "gone"


# Outcomes that need nobody's attention.
_FINE = {Outcome.LISTED, Outcome.UPDATED, Outcome.UNCHANGED}
_STYLE = {
    Outcome.LISTED: "green",
    Outcome.UPDATED: "green",
    Outcome.UNCHANGED: "dim",
    Outcome.NOT_CLEAN: "yellow",
    Outcome.FAILED: "red",
    Outcome.BLOCKED: "red",
    Outcome.DISALLOWED: "yellow",
    Outcome.POSTPONED: "cyan",
    Outcome.REMOVED: "magenta",
    Outcome.GONE: "magenta",
}


@dataclass(frozen=True, slots=True)
class Event:
    """One document's outcome.

    Attributes:
        publisher: Its publisher.
        document: Its URL.
        outcome: What became of it.
        reason: Why, for anything but a plain success.
        verified: Whether its listed build has been checked by hand.
        again: Whether it failed the same way on the last run, so it's
            already known and doesn't fail the job.
    """

    publisher: str
    document: str
    outcome: Outcome
    reason: str = ""
    verified: bool = False
    again: bool = False


@dataclass(slots=True)
class Tally:
    """Every outcome in a run, and the pages discovery couldn't read."""

    events: list[Event] = field(default_factory=list)
    page_failures: dict[str, dict[str, str]] = field(default_factory=dict)

    def counts(self, publisher: str) -> Counter[Outcome]:
        """How many documents of a publisher ended each way."""
        return Counter(e.outcome for e in self.events if e.publisher == publisher)

    def publishers(self) -> list[str]:
        """The publishers in the run, in order."""
        return list(
            dict.fromkeys([*self.page_failures, *(e.publisher for e in self.events)])
        )


class Reporter(CliOutput):
    """Reports catalog progress and keeps the tally used in its summaries."""

    _annotation_title = "Catalog"
    _stderr = True

    def __init__(
        self,
        github: bool,
        stream: TextIO | None = None,
        console: Console | None = None,
        summary_path: str | None = None,
        summary_file: Path | None = None,
    ) -> None:
        super().__init__(github, stream, console, summary_path=summary_path)
        self._summary_file = summary_file
        self.tally = Tally()

    @classmethod
    def for_environment(
        cls, env: Mapping[str, str] = os.environ, summary_file: Path | None = None
    ) -> Reporter:
        """The reporter for where the job runs.

        Args:
            env: The environment.
            summary_file: Also write the summary here, as Markdown. GitHub
                gives each step its own `GITHUB_STEP_SUMMARY`, so a later step
                (the update's pull request) reads it from this file.
        """
        github = in_github_actions(env)
        return cls(
            github,
            summary_path=env.get("GITHUB_STEP_SUMMARY") if github else None,
            summary_file=summary_file,
        )

    def install_logging(self, verbose: bool = False) -> None:
        """Routes the job's log records (retries, waits) through this reporter."""
        level = logging.DEBUG if verbose else logging.INFO
        handler: logging.Handler
        if self.github:
            handler = _GitHubHandler(self)
        else:
            from rich.logging import RichHandler

            handler = RichHandler(console=self._console, show_path=False, markup=False)
        handler.setLevel(level)
        root = logging.getLogger("star_ground.catalog")
        root.handlers = [handler]
        root.setLevel(level)
        root.propagate = False

    def page_failed(self, publisher: str, page: str, reason: str) -> None:
        """A listing page discovery couldn't read (logged as it happened)."""
        self.tally.page_failures.setdefault(publisher, {})[page] = reason

    def outcome(self, event: Event) -> None:
        """One document's outcome, as it happens."""
        self.tally.events.append(event)
        detail = f" ({event.reason})" if event.reason else ""
        if self.github:
            line = f"[{event.outcome}] {event.document}{detail}"
            if event.outcome in (Outcome.FAILED, Outcome.BLOCKED) and not event.again:
                self.warning(
                    f"{event.document}{detail}",
                    title=f"{event.publisher}: {event.outcome}",
                )
            else:
                self.info(line)
        else:
            style = _STYLE[event.outcome]
            self._console.print(f"[{style}]{event.outcome:>10}[/] ", end="")
            self._console.print(f"{event.document}{detail}", markup=False)

    def summary(self) -> None:
        """Counts per publisher, then every document that wasn't added, and why."""
        outcomes = list(Outcome)
        publishers = self.tally.publishers()
        problems = [e for e in self.tally.events if e.outcome not in _FINE]
        unchecked = [
            e for e in self.tally.events if e.outcome in _FINE and not e.verified
        ]
        text = self._markdown(outcomes, publishers, problems, unchecked)
        if self._summary_file is not None:
            self._summary_file.write_text(text, encoding="utf-8")
        if self.github:
            self.step_summary(text)
            self.info(text)
            return

        from rich.table import Table

        table = Table(title="Circuit catalog", title_justify="left")
        table.add_column("Publisher")
        for o in outcomes:
            table.add_column(o.value.capitalize(), justify="right", style=_STYLE[o])
        table.add_column("Verified", justify="right", style="bold green")
        table.add_column("Pages not read", justify="right")
        for publisher in publishers:
            counts = self.tally.counts(publisher)
            table.add_row(
                publisher,
                *(str(counts[o]) for o in outcomes),
                str(self._verified(publisher)),
                str(len(self.tally.page_failures.get(publisher, {}))),
            )
        self._console.print(table)
        if problems:
            detail = Table(title="Not added", title_justify="left")
            for column in ("Publisher", "Document", "Outcome", "Reason"):
                detail.add_column(column, overflow="fold")
            for e in problems:
                detail.add_row(
                    e.publisher,
                    e.document,
                    f"[{_STYLE[e.outcome]}]{e.outcome}",
                    e.reason,
                )
            self._console.print(detail)
        if unchecked:
            listed = Table(
                title=f"Listed but not checked by hand ({len(unchecked)})",
                title_justify="left",
            )
            for column in ("Publisher", "Document", "Outcome"):
                listed.add_column(column, overflow="fold")
            for e in unchecked:
                listed.add_row(e.publisher, e.document, e.outcome)
            self._console.print(listed)

    def _markdown(
        self,
        outcomes: list[Outcome],
        publishers: list[str],
        problems: list[Event],
        unchecked: list[Event],
    ) -> str:
        """The summary as Markdown: for GitHub's run page and the update's PR."""
        lines = [
            "## Circuit catalog",
            "",
            "| Publisher | "
            + " | ".join(o.value.capitalize() for o in outcomes)
            + " | Verified | Pages not read |",
            "| --- " * (len(outcomes) + 3) + "|",
        ]
        for publisher in publishers:
            counts = self.tally.counts(publisher)
            failed_pages = len(self.tally.page_failures.get(publisher, {}))
            lines.append(
                f"| {publisher} | "
                + " | ".join(str(counts[o]) for o in outcomes)
                + f" | {self._verified(publisher)} | {failed_pages} |"
            )
        if problems:
            lines += [
                "",
                "### Not added",
                "",
                "| Publisher | Document | Outcome | Reason |",
            ]
            lines.append("| --- | --- | --- | --- |")
            lines += [
                f"| {e.publisher} | {e.document} | {e.outcome} | {e.reason.replace('|', '/')} |"
                for e in problems
            ]
        pages = [
            (p, page, why)
            for p, failures in self.tally.page_failures.items()
            for page, why in failures.items()
        ]
        if pages:
            lines += ["", "### Pages not read", "", "| Publisher | Page | Reason |"]
            lines.append("| --- | --- | --- |")
            lines += [
                f"| {p} | {page} | {why.replace('|', '/')} |" for p, page, why in pages
            ]
        if unchecked:
            lines += [
                "",
                "<details><summary>Listed but not checked by hand "
                f"({len(unchecked)})</summary>",
                "",
                "| Publisher | Document | Outcome |",
                "| --- | --- | --- |",
            ]
            lines += [
                f"| {e.publisher} | {e.document} | {e.outcome} |" for e in unchecked
            ]
            lines += ["", "</details>"]
        return "\n".join(lines) + "\n"

    def _verified(self, publisher: str) -> int:
        return sum(
            1
            for e in self.tally.events
            if e.publisher == publisher and e.outcome in _FINE and e.verified
        )


class _GitHubHandler(logging.Handler):
    """Log records as GitHub Actions output: warnings and errors annotate the run."""

    def __init__(self, reporter: Reporter) -> None:
        super().__init__()
        self._reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        if record.levelno >= logging.ERROR:
            self._reporter.error(message)
        elif record.levelno >= logging.WARNING:
            self._reporter.warning(message)
        elif record.levelno <= logging.DEBUG:
            self._reporter.debug(message)
        else:
            self._reporter.info(message)
