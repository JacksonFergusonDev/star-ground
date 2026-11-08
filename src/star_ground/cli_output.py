"""Human-readable command output for terminals and GitHub Actions."""

import os
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import cached_property
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from rich.console import Console


def in_github_actions(env: Mapping[str, str] = os.environ) -> bool:
    """Whether the command runs in GitHub Actions."""
    return env.get("GITHUB_ACTIONS") == "true"


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


class CliOutput:
    """Use Rich locally and emit workflow commands in GitHub Actions.

    Args:
        github: Override detection from the environment when specified.
        stream: Where GitHub Actions output goes, defaulting to stdout.
        console: The local Rich console, defaulting to stdout.
        summary_path: Override the step summary file when specifying ``github``.
    """

    _annotation_title: str | None = None
    _stderr = False

    def __init__(
        self,
        github: bool | None = None,
        stream: TextIO | None = None,
        console: Console | None = None,
        *,
        summary_path: str | None = None,
    ) -> None:
        self.github = in_github_actions() if github is None else github
        self._stream = stream
        self._local_console = console
        self._summary_path = (
            os.environ.get("GITHUB_STEP_SUMMARY") if github is None else summary_path
        )

    @cached_property
    def _console(self) -> Console:
        from rich.console import Console

        return self._local_console or Console(stderr=self._stderr, highlight=False)

    def _print(self, line: str, *, end: str = "\n") -> None:
        print(line, file=self._stream, end=end, flush=True)

    @contextmanager
    def section(self, title: str) -> Iterator[None]:
        """Open a Rich section or a collapsible group in the workflow log."""
        if self.github:
            self._print(f"::group::{_escape(title)}")
            try:
                yield
            finally:
                self._print("::endgroup::")
        else:
            from rich.text import Text

            self._console.rule(Text(title, style="bold"))
            yield

    def info(self, message: str, *, end: str = "\n") -> None:
        """Write literal progress or diagnostics."""
        if self.github:
            # Text from subprocesses and network responses can resemble commands.
            lines = message.replace("##[", r"##\[").splitlines(keepends=True)
            self._print(
                "".join(
                    f"| {line}" if line.lstrip().startswith("::") else line
                    for line in lines
                ),
                end=end,
            )
        else:
            self._console.print(message, markup=False, end=end, soft_wrap=True)

    def success(self, message: str) -> None:
        """Write a successful outcome."""
        if self.github:
            self.info(message)
        else:
            self._console.print(
                message, style="bold green", markup=False, soft_wrap=True
            )

    def debug(self, message: str) -> None:
        """Write a message visible when workflow debug logging is enabled."""
        if self.github:
            self._print(f"::debug::{_escape(message)}")

    def warning(self, message: str, title: str | None = None) -> None:
        """Report an issue that lets the command continue."""
        self._annotate("warning", message, title, "yellow")

    def error(self, message: str, title: str | None = None) -> None:
        """Report a failure."""
        self._annotate("error", message, title, "bold red")

    @contextmanager
    def report_failures(self, title: str) -> Iterator[None]:
        """Show uncaught CLI errors with a traceback and exit with failure."""
        try:
            yield
        except Exception as error:
            if self.github:
                self.error(str(error) or type(error).__name__, title)
                self.info(traceback.format_exc().rstrip())
                self.step_summary(
                    f"## {title}\n\nThis step failed. See the log for details.\n"
                )
            else:
                self._console.print_exception()
            raise SystemExit(1) from None

    def _annotate(
        self, level: str, message: str, title: str | None, style: str
    ) -> None:
        title = title or self._annotation_title
        if self.github:
            properties = ""
            if title:
                properties = " title=" + _escape(title).replace(":", "%3A").replace(
                    ",", "%2C"
                )
            self._print(f"::{level}{properties}::{_escape(message)}")
        else:
            from rich.text import Text

            line = Text(f"{level}: ", style=style)
            if title:
                line.append(f"{title}: ")
            line.append(message)
            self._console.print(line, soft_wrap=True)

    def step_summary(self, markdown: str) -> None:
        """Append Markdown to the GitHub Actions step summary when available."""
        if self.github and self._summary_path:
            with open(self._summary_path, "a", encoding="utf-8") as summary:
                summary.write(markdown)
