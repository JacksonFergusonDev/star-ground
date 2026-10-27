"""Unit tests for the release preflight script."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence

import pytest

from tools import prepare_release
from tools.prepare_release import CheckoutState, OutputStyle, WorkflowRun


def _completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["cmd"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _run_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "databaseId": 101,
        "headSha": "abc123456789",
        "status": "completed",
        "conclusion": "success",
        "url": "https://github.com/example/star-ground/actions/runs/101",
    }
    payload.update(overrides)
    return payload


def test_check_checkout_succeeds_on_clean_synced_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            "main",
            "",
            "abc123456789",
            "abc123456789\trefs/heads/main",
            "example/star-ground",
        ]
    )
    monkeypatch.setattr(prepare_release, "read_command", lambda _cmd: next(responses))

    checkout = prepare_release.check_checkout()

    assert checkout == CheckoutState(repo="example/star-ground", sha="abc123456789")


@pytest.mark.parametrize(
    ("branch", "status", "sha", "remote", "message"),
    [
        ("feat/branch", "", "abc", "abc\trefs/heads/main", "must start from main"),
        ("main", " M app.py", "abc", "abc\trefs/heads/main", "working tree changes"),
        ("main", "", "abc", "def\trefs/heads/main", "differs from origin/main"),
        ("main", "", "abc", "", "differs from origin/main"),
    ],
)
def test_check_checkout_rejects_invalid_git_state(
    branch: str,
    status: str,
    sha: str,
    remote: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter([branch, status, sha, remote])
    monkeypatch.setattr(prepare_release, "read_command", lambda _cmd: next(responses))

    with pytest.raises(SystemExit) as exc_info:
        prepare_release.check_checkout()

    assert exc_info.value.code == 1
    assert message in capsys.readouterr().err


def test_prepare_inputs_returns_false_when_presets_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Sequence[str]] = []

    def fake_run(
        cmd: Sequence[str], *, capture_output: bool = False
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        calls.append(cmd)
        return _completed()

    monkeypatch.setattr(prepare_release, "run_repo_cmd", fake_run)

    assert prepare_release.prepare_inputs() is False
    assert calls == [
        [sys.executable, "-m", "tools.generate_presets"],
        ["git", "status", "--porcelain", "--", *prepare_release.RELEASE_INPUTS],
    ]


def test_prepare_inputs_commits_and_pushes_when_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    outputs = iter(
        [
            _completed(),
            _completed(stdout=" M src/bom_lib/_presets_data.py\n"),
            _completed(stdout="diff --git a/src/bom_lib/_presets_data.py ...\n"),
            _completed(),
            _completed(),
            _completed(),
        ]
    )

    def fake_run(
        cmd: Sequence[str], *, capture_output: bool = False
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        commands.append(list(cmd))
        return next(outputs)

    monkeypatch.setattr(prepare_release, "run_repo_cmd", fake_run)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(prepare_release, "confirm", lambda _q: True)

    assert prepare_release.prepare_inputs() is True
    assert commands[3:] == [
        ["git", "add", "--", *prepare_release.RELEASE_INPUTS],
        [
            "git",
            "commit",
            "--only",
            "-m",
            "chore(release): refresh generated presets",
            "--",
            *prepare_release.RELEASE_INPUTS,
        ],
        ["git", "push"],
    ]


def test_prepare_inputs_stops_when_declined(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outputs = iter(
        [
            _completed(),
            _completed(stdout=" M src/bom_lib/_presets_data.py\n"),
            _completed(stdout="diff --git a/src/bom_lib/_presets_data.py ...\n"),
        ]
    )
    monkeypatch.setattr(
        prepare_release,
        "run_repo_cmd",
        lambda _cmd, *, capture_output=False: next(outputs),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(prepare_release, "confirm", lambda _q: False)

    with pytest.raises(SystemExit) as exc_info:
        prepare_release.prepare_inputs()

    assert exc_info.value.code == 1
    assert "not accepted" in capsys.readouterr().err


def test_check_ci_passes_when_completed_run_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = CheckoutState(repo="example/star-ground", sha="abc123456789")
    monkeypatch.setattr(
        prepare_release,
        "read_json",
        lambda _cmd: [_run_payload()],
    )

    prepare_release.check_ci(checkout)


def test_check_ci_watches_in_progress_run_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = CheckoutState(repo="example/star-ground", sha="abc123456789")
    json_payloads = iter(
        [
            [_run_payload(status="in_progress", conclusion=None)],
            _run_payload(status="completed", conclusion="success"),
        ]
    )
    watched: list[list[str]] = []

    def fake_watch(
        cmd: Sequence[str], *, capture_output: bool = False
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        watched.append(list(cmd))
        return _completed()

    monkeypatch.setattr(prepare_release, "read_json", lambda _cmd: next(json_payloads))
    monkeypatch.setattr(prepare_release, "confirm", lambda _q: True)
    monkeypatch.setattr(prepare_release, "require_source", lambda _c: None)
    monkeypatch.setattr(prepare_release, "run_repo_cmd", fake_watch)

    prepare_release.check_ci(checkout)

    assert watched == [
        [
            "gh",
            "run",
            "watch",
            "101",
            "--repo",
            "example/star-ground",
            "--exit-status",
            "--interval",
            "10",
        ]
    ]


def test_check_ci_stops_on_failed_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout = CheckoutState(repo="example/star-ground", sha="abc123456789")
    monkeypatch.setattr(
        prepare_release,
        "read_json",
        lambda _cmd: [_run_payload(conclusion="failure")],
    )

    with pytest.raises(SystemExit) as exc_info:
        prepare_release.check_ci(checkout)

    assert exc_info.value.code == 1
    assert "completed / failure" in capsys.readouterr().err


def test_check_local_state_runs_uv_lock_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(
        cmd: Sequence[str], *, capture_output: bool = False
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        commands.append(list(cmd))
        return _completed()

    monkeypatch.setattr(prepare_release, "run_repo_cmd", fake_run)

    prepare_release.check_local_state()

    assert commands == [["uv", "lock", "--check"]]


def test_main_rechecks_checkout_when_inputs_are_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = CheckoutState(repo="example/star-ground", sha="111111111111")
    second = CheckoutState(repo="example/star-ground", sha="222222222222")
    checkouts = iter([first, second, second, second])
    verified_ci: list[CheckoutState] = []

    monkeypatch.setattr(prepare_release, "check_checkout", lambda: next(checkouts))
    monkeypatch.setattr(prepare_release, "prepare_inputs", lambda: True)
    monkeypatch.setattr(prepare_release, "check_ci", verified_ci.append)
    monkeypatch.setattr(prepare_release, "check_local_state", lambda: None)

    prepare_release.main()

    assert verified_ci == [second]


def test_read_json_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(prepare_release, "read_command", lambda _cmd: "{not-json")

    with pytest.raises(SystemExit) as exc_info:
        prepare_release.read_json(["gh", "run", "list"])

    assert exc_info.value.code == 1
    assert "invalid JSON" in capsys.readouterr().err


def test_confirm_handles_interactive_and_non_tty_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert prepare_release.confirm("Continue?") is False

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["maybe", "y"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert prepare_release.confirm("Continue?") is True


def test_report_applies_ansi_when_forced(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    prepare_release.report("Ready", style=OutputStyle.SUCCESS)

    out = capsys.readouterr().out
    assert "\033[1;32mReady\033[0m" in out
    assert isinstance(
        WorkflowRun(
            database_id=1,
            head_sha="abc",
            status="completed",
            conclusion="success",
            url=json.dumps("https://example.com"),
        ),
        WorkflowRun,
    )
