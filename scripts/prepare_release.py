"""Verify release readiness before a version bump."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from rich.console import Console

from star_ground.cli_output import CliOutput

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
VENV_BIN: Path = REPO_ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
CI_WORKFLOW = "ci.yml"
RUN_DISCOVERY_ATTEMPTS = 24
RUN_DISCOVERY_INTERVAL = 5


@dataclass(frozen=True)
class CheckoutState:
    """Verified repository slug and HEAD commit SHA for a release."""

    repo: str
    sha: str


@dataclass(frozen=True)
class WorkflowRun:
    """GitHub Actions workflow run metadata for a specific commit."""

    database_id: int
    head_sha: str
    status: str
    conclusion: str
    url: str


def get_repo_env() -> dict[str, str]:
    """Build an environment dictionary with the project's virtualenv on PATH.

    Returns:
        Copy of ``os.environ`` with ``.venv/bin`` prepended to ``PATH`` when present.
    """
    env = os.environ.copy()
    if VENV_BIN.is_dir():
        current_path = env.get("PATH", "")
        if str(VENV_BIN) not in current_path.split(os.pathsep):
            env["PATH"] = (
                f"{VENV_BIN}{os.pathsep}{current_path}"
                if current_path
                else str(VENV_BIN)
            )
    return env


def run_repo_cmd(
    cmd: Sequence[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a command anchored at the repository root.

    Args:
        cmd: Command and arguments to execute.
        capture_output: If True, capture stdout and stderr as text.

    Returns:
        CompletedProcess instance for the finished command.
    """
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        capture_output=capture_output,
        text=True,
        env=get_repo_env(),
    )


def stop(message: str) -> NoReturn:
    """Stop before the version bump with an actionable error message.

    Args:
        message: Reason the release preflight stopped.
    """
    CliOutput(console=Console(stderr=True)).error(f"Version bump stopped: {message}")
    sys.exit(1)


def read_command(command: list[str]) -> str:
    """Run a prerequisite command and return stripped stdout, stopping on error.

    Args:
        command: Command and arguments to execute.

    Returns:
        Stripped standard output from the command.
    """
    try:
        result = run_repo_cmd(command, capture_output=True)
    except OSError as error:
        stop(f"could not run {command[0]}: {error}")
    if result.returncode:
        CliOutput(console=Console(stderr=True, highlight=False)).info(
            result.stderr.rstrip()
        )
        stop(f"{' '.join(command)} failed. Check tool installation and authentication.")
    return result.stdout.strip()


def read_json(command: list[str]) -> object:
    """Run a command and parse its stdout as JSON, stopping on invalid output.

    Args:
        command: Command and arguments to execute.

    Returns:
        Decoded JSON value.
    """
    output = read_command(command)
    try:
        data: object = json.loads(output)
        return data
    except json.JSONDecodeError:
        stop(f"{command[0]} returned invalid JSON.")


def confirm(question: str) -> bool:
    """Ask an interactive yes/no question; non-TTY input declines.

    Args:
        question: Prompt text shown before ``[y/N]``.

    Returns:
        True if the user answered yes, False otherwise.
    """
    if not sys.stdin.isatty():
        return False
    output = CliOutput()
    try:
        while True:
            output.info(f"{question} [y/N] ", end="")
            answer = input().strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            output.info("Please answer y or n.")
    except EOFError:
        return False


def _parse_workflow_run(raw: object) -> WorkflowRun:
    """Validate and convert a JSON object from ``gh run`` into a ``WorkflowRun``.

    Args:
        raw: Decoded JSON dictionary representing a single workflow run.

    Returns:
        Validated ``WorkflowRun`` instance.
    """
    if not isinstance(raw, dict):
        stop("gh returned an unexpected workflow run payload.")
    database_id = raw.get("databaseId")
    head_sha = raw.get("headSha")
    status = raw.get("status")
    conclusion = raw.get("conclusion")
    url = raw.get("url")
    if (
        not isinstance(database_id, int)
        or not isinstance(head_sha, str)
        or not isinstance(status, str)
        or not isinstance(url, str)
        or (conclusion is not None and not isinstance(conclusion, str))
    ):
        stop("gh returned incomplete workflow run fields.")
    return WorkflowRun(
        database_id=database_id,
        head_sha=head_sha,
        status=status,
        conclusion=conclusion or "",
        url=url,
    )


def latest_run(checkout: CheckoutState) -> WorkflowRun | None:
    """Find the newest CI run for the pinned commit, if one exists.

    Args:
        checkout: Verified repository and commit SHA.

    Returns:
        The newest ``WorkflowRun`` for ``checkout.sha``, or None if none exists.
    """
    runs = read_json(
        [
            "gh",
            "run",
            "list",
            "--repo",
            checkout.repo,
            "--workflow",
            CI_WORKFLOW,
            "--commit",
            checkout.sha,
            "--limit",
            "1",
            "--json",
            "databaseId,headSha,status,conclusion,url",
        ]
    )
    if not isinstance(runs, list):
        stop("gh run list returned an unexpected payload.")
    if not runs:
        return None
    run = _parse_workflow_run(runs[0])
    if run.head_sha != checkout.sha:
        stop(f"{CI_WORKFLOW} returned a run for a different commit.")
    return run


def check_checkout() -> CheckoutState:
    """Validate the release branch and remote synchronization.

    Returns:
        Verified ``CheckoutState`` with the repository slug and HEAD commit SHA.
    """
    if read_command(["git", "branch", "--show-current"]) != "main":
        stop("releases must start from main.")
    if read_command(["git", "status", "--porcelain"]):
        stop("commit or stash all working tree changes before releasing.")
    sha = read_command(["git", "rev-parse", "HEAD"])
    remote = read_command(["git", "ls-remote", "origin", "refs/heads/main"])
    if not remote or remote.split()[0] != sha:
        stop("local HEAD differs from origin/main. Pull or push before releasing.")
    repo = read_command(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
    )
    return CheckoutState(repo=repo, sha=sha)


def require_source(expected: CheckoutState) -> None:
    """Ensure the branch and commit did not change while waiting for remote work.

    Args:
        expected: The ``CheckoutState`` captured at the start of preflight.
    """
    if check_checkout() != expected:
        stop(
            "the release source changed during preflight. Rerun your just bump command."
        )


def wait_for_ci(checkout: CheckoutState, run: WorkflowRun | None) -> WorkflowRun:
    """Discover a CI run on ``checkout.sha`` if needed and watch it to completion.

    Args:
        checkout: Verified repository and commit SHA.
        run: Existing in-progress run, or None if GitHub has not registered it yet.

    Returns:
        Completed ``WorkflowRun`` fetched after watching finishes.
    """
    if not confirm("Wait for CI to pass and automatically continue?"):
        stop(
            f"CI must pass on {checkout.sha[:8]}. "
            "Wait for it, then rerun your just bump command."
        )
    require_source(checkout)
    output = CliOutput()
    if run is None:
        output.info(f"Waiting for GitHub to register CI on {checkout.sha[:8]}...")
        for attempt in range(RUN_DISCOVERY_ATTEMPTS):
            run = latest_run(checkout)
            if run is not None:
                break
            if attempt + 1 < RUN_DISCOVERY_ATTEMPTS:
                time.sleep(RUN_DISCOVERY_INTERVAL)
        if run is None:
            stop(
                f"no CI run appeared for {checkout.sha[:8]}. "
                f"Inspect gh run list --repo {checkout.repo} --workflow {CI_WORKFLOW} "
                f"--commit {checkout.sha}, then rerun your just bump command."
            )
    output.info(f"CI: {run.url}")
    if run.status != "completed":
        command = [
            "gh",
            "run",
            "watch",
            str(run.database_id),
            "--repo",
            checkout.repo,
            "--exit-status",
            "--interval",
            "10",
        ]
        try:
            result = run_repo_cmd(command)
        except OSError as error:
            stop(f"could not watch CI: {error}")
        if result.returncode:
            stop(f"CI did not finish successfully, or watching failed. {run.url}")
        run = _parse_workflow_run(
            read_json(
                [
                    "gh",
                    "run",
                    "view",
                    str(run.database_id),
                    "--repo",
                    checkout.repo,
                    "--json",
                    "databaseId,headSha,status,conclusion,url",
                ]
            )
        )
    require_source(checkout)
    return run


def check_ci(checkout: CheckoutState) -> None:
    """Require a passing CI run on the release commit.

    Args:
        checkout: Verified repository and commit SHA.
    """
    output = CliOutput()
    run = latest_run(checkout)
    if run is None:
        CliOutput(console=Console(stderr=True)).warning(
            f"No {CI_WORKFLOW} run exists for {checkout.sha[:8]}."
        )
        output.info("CI starts automatically after a push to main.")
        run = wait_for_ci(checkout, None)
    elif run.status != "completed":
        output.info(f"{CI_WORKFLOW}: {run.status}. {run.url}")
        run = wait_for_ci(checkout, run)
    if (
        run.head_sha != checkout.sha
        or run.status != "completed"
        or run.conclusion != "success"
    ):
        stop(f"{CI_WORKFLOW}: {run.status} / {run.conclusion or 'pending'}. {run.url}")
    output.success(f"{CI_WORKFLOW} passed on {checkout.sha[:8]}: {run.url}")


def check_local_state() -> None:
    """Verify the lockfile matches pyproject.toml before bumping."""
    command = ["uv", "lock", "--check"]
    try:
        result = run_repo_cmd(command)
    except OSError as error:
        stop(f"could not run {command[0]}: {error}")
    if result.returncode:
        stop(f"{' '.join(command)} failed.")


def main() -> None:
    """Gate the version bump on verified source, CI, and lockfile state."""
    try:
        checkout = check_checkout()
        check_ci(checkout)
        require_source(checkout)
        check_local_state()
        require_source(checkout)
    except KeyboardInterrupt:
        stop("preparation cancelled. Any workflows already started continue on GitHub.")
    CliOutput().success("Release preflight passed.")


if __name__ == "__main__":
    main()
