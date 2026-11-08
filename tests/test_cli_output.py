"""Exercise script output on a terminal and under GitHub Actions."""

from pathlib import Path

import pytest

from scripts import fetch_samples
from star_ground.cli_output import CliOutput


def test_local_output_uses_rich_and_preserves_literal_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")

    CliOutput().success("Wrote [example].json")

    assert "\033[1;32mWrote [example].json\033[0m" in capsys.readouterr().out


def test_sample_fetch_reports_failures_in_github_actions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("Earlier summary\n\n")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(
        fetch_samples,
        "load_manifest",
        lambda: [fetch_samples.Sample("board.pdf", "https://example.test", "")],
    )
    monkeypatch.setattr(
        fetch_samples,
        "fetch",
        lambda samples: ["board.pdf: 50% complete\nDisconnected"],
    )

    assert fetch_samples.main() == 1

    assert capsys.readouterr().out.splitlines() == [
        "::group::Fetch test samples",
        "::error title=Test samples::board.pdf: 50%25 complete%0ADisconnected",
        "::endgroup::",
    ]
    assert summary.read_text() == (
        "Earlier summary\n\n## Test samples\n\n1 of 1 test samples could not be prepared.\n"
    )


def test_workflow_traceback_cannot_emit_commands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    output = CliOutput()

    with (
        pytest.raises(SystemExit) as failed,
        output.report_failures("Smoke: browser"),
        output.section("Check Chromium"),
    ):
        raise RuntimeError("Browser failed\n::endgroup::\nlog ##[error]from the page")

    assert failed.value.code == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines.count("::endgroup::") == 1
    assert "| ::endgroup::" in lines
    assert r"log ##\[error]from the page" in lines
    assert any(line.startswith("::error title=Smoke%3A browser::") for line in lines)
