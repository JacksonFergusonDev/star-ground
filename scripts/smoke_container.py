"""Exercise a built image's default command, health check and offline capabilities."""

import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from scripts._browser_smoke import check_browser
from star_ground.cli_output import CliOutput


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


def smoke(image: str, artifacts: Path) -> None:
    """Check HTTP readiness, offline functionality and graceful termination."""
    output = CliOutput()
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
        with output.section("Start the container and check HTTP"):
            docker(
                "run",
                "--detach",
                "--name",
                name,
                *restrictions,
                "--publish",
                "127.0.0.1::8000",
                image,
            )
            wait_healthy(name)
            port = docker("port", name, "8000/tcp")
            for path in ("/api/health", "/", "/app/"):
                with urllib.request.urlopen(
                    f"http://{port}{path}", timeout=5
                ) as response:
                    body = response.read()
                    assert response.status == 200, (path, response.status)
                    assert body, path
                    if path.endswith("health"):
                        assert json.loads(body) == {"status": "ok"}, body
        with output.section("Run the offline checks"):
            output.info(
                docker(
                    "run",
                    "--name",
                    offline,
                    *restrictions,
                    "--network=none",
                    "--mount",
                    f"type=bind,src={checks},dst=/smoke/checks.py,readonly",
                    image,
                    "python",
                    "/smoke/checks.py",
                    timeout=240,
                )
            )
            output.success("All offline container checks passed.")
        with output.section("Check the site in Chromium"):
            check_browser(f"http://{port}", artifacts)
        with output.section("Check graceful shutdown"):
            wait_healthy(name, timeout=10)
            # SIGTERM must stop the server cleanly; Docker's forced SIGKILL exits 137.
            docker("stop", "--time=10", name)
            exit_code = docker("inspect", "--format", "{{.State.ExitCode}}", name)
            assert exit_code == "0", f"Unclean shutdown: {exit_code}"
    finally:
        with output.section("Collect container diagnostics and clean up"):
            for container in (name, offline):
                for command in (
                    ("logs", container),
                    ("inspect", container),
                    ("rm", "--force", container),
                ):
                    try:
                        output.info(docker(*command))
                    except (RuntimeError, subprocess.TimeoutExpired) as error:
                        output.warning(
                            str(error), title="Container diagnostics and cleanup"
                        )


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
    output = CliOutput()
    with (
        output.report_failures("Container checks"),
        tempfile.TemporaryDirectory(prefix="star-ground-smoke-") as temporary,
    ):
        smoke(args.image, args.artifacts or Path(temporary))
    result = "Container startup, HTTP, offline functionality and shutdown passed."
    output.success(result)
    output.step_summary(f"## Container checks\n\n{result}\n")


if __name__ == "__main__":
    main()
