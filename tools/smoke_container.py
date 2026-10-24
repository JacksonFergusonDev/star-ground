"""Exercise a built image's default command, health check and offline capabilities."""

import argparse
import csv
import io
import json
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path


def docker(*args: str, timeout: int = 30) -> str:
    """Run a bounded Docker command and include its output in any failure."""
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"docker {args}: {result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def wait_healthy(container: str, timeout: float = 90) -> None:
    """Wait for Docker health, failing immediately on exit or unhealthy status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
        if not state["Running"]:
            raise RuntimeError(f"Container exited before readiness: {state}")
        health = state.get("Health", {}).get("Status")
        if health == "healthy":
            return
        if health == "unhealthy":
            raise RuntimeError(f"Container failed its health check: {state}")
        time.sleep(1)
    raise TimeoutError(f"Container did not become healthy within {timeout}s")


def check_browser(url: str, artifacts: Path) -> None:
    """Drive Chromium through the served app and validate its downloaded build pack."""
    from playwright.sync_api import expect, sync_playwright

    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(60_000)
        try:
            page.goto(url, wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="Star Ground")).to_be_visible()
            page.get_by_role("textbox", name="BOM Text", exact=True).fill(
                "R1 10k\nC1 100n\nU1 TL072"
            )
            page.get_by_role("button", name="Generate Master List", exact=True).click()
            with page.expect_download() as download_info:
                page.get_by_role(
                    "button", name="Download Complete Build Pack", exact=False
                ).click()
            download = download_info.value
            assert download.failure() is None, download.failure()
            pack_path = artifacts / "build-pack.zip"
            download.save_as(pack_path)
            with zipfile.ZipFile(pack_path) as pack:
                assert pack.testzip() is None
                rows = list(
                    csv.DictReader(
                        io.StringIO(pack.read("Shopping List.csv").decode("utf-8-sig"))
                    )
                )
                for part in ("10k", "100n", "TL072"):
                    row = next(row for row in rows if row["Part"] == part)
                    assert row["BOM Qty"] == "1", row
                assert any(name.endswith(".pdf") for name in pack.namelist())
            assert not errors, errors
        finally:
            try:
                page.screenshot(path=str(artifacts / "browser.png"), full_page=True)
            finally:
                context.tracing.stop(path=str(artifacts / "trace.zip"))
                browser.close()


def smoke(image: str, artifacts: Path) -> None:
    """Check HTTP readiness, offline functionality and graceful termination."""
    name = f"star-ground-smoke-{uuid.uuid4().hex}"
    checks = Path(__file__).with_name("container_checks.py").resolve()
    restrictions = (
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs=/tmp:rw,nosuid,nodev",
        "--tmpfs=/home/app:rw,nosuid,nodev,uid=10001,gid=10001",
    )
    offline = f"{name}-offline"
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            *restrictions,
            "--publish",
            "127.0.0.1::8501",
            image,
        )
        wait_healthy(name)
        port = docker("port", name, "8501/tcp")
        for path in ("/_stcore/health", "/"):
            with urllib.request.urlopen(f"http://{port}{path}", timeout=5) as response:
                body = response.read()
                assert response.status == 200, (path, response.status)
                assert body, path
                if path.endswith("health"):
                    assert body == b"ok", body
        print(
            docker(
                "run",
                "--name",
                offline,
                *restrictions,
                "--network=none",
                "--mount",
                f"type=bind,src={checks},dst=/smoke/checks.py,readonly",
                "--env",
                "PYTHONPATH=/app",
                image,
                "python",
                "/smoke/checks.py",
                timeout=240,
            ),
            flush=True,
        )
        check_browser(f"http://{port}", artifacts)
        wait_healthy(name, timeout=10)
        # SIGTERM must stop Streamlit cleanly; Docker's forced SIGKILL exits 137.
        docker("stop", "--time=10", name)
        exit_code = docker("inspect", "--format", "{{.State.ExitCode}}", name)
        assert exit_code == "0", f"Unclean shutdown: {exit_code}"
        print(
            "Container startup, HTTP, offline functionality and shutdown passed",
            flush=True,
        )
    finally:
        for container in (name, offline):
            for command in (
                ("logs", container),
                ("inspect", container),
                ("rm", "--force", container),
            ):
                try:
                    print(docker(*command), flush=True)
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    print(f"Diagnostics/cleanup: {error}", flush=True)


def main() -> None:
    """Run smoke checks against the supplied local image tag or image ID."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="Keep browser traces, screenshots and downloads here",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="star-ground-smoke-") as temporary:
        smoke(args.image, args.artifacts or Path(temporary))


if __name__ == "__main__":
    main()
