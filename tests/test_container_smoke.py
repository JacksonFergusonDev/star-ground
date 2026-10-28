"""Failure-path coverage for the container gate, without running Docker on the host."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from tools import smoke_container


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"Running": False, "ExitCode": 1}, "exited before readiness"),
        (
            {"Running": True, "Health": {"Status": "unhealthy"}},
            "failed its health check",
        ),
    ],
)
def test_readiness_fails_early(monkeypatch, state, message):
    command = Mock(return_value=json.dumps(state))
    monkeypatch.setattr(smoke_container, "docker", command)
    with pytest.raises(RuntimeError, match=message):
        smoke_container.wait_healthy("test")
    command.assert_called_once()


def test_readiness_waits_for_health(monkeypatch):
    command = Mock(
        side_effect=[
            json.dumps({"Running": True, "Health": {"Status": "starting"}}),
            json.dumps({"Running": True, "Health": {"Status": "healthy"}}),
        ]
    )
    monkeypatch.setattr(smoke_container, "docker", command)
    monkeypatch.setattr("tools.smoke_container.time.sleep", Mock())
    smoke_container.wait_healthy("test")
    assert command.call_count == 2


def test_readiness_has_a_deadline(monkeypatch):
    monkeypatch.setattr(
        "tools.smoke_container.time.monotonic", Mock(side_effect=[0, 91])
    )
    with pytest.raises(TimeoutError, match="did not become healthy"):
        smoke_container.wait_healthy("test")


def test_docker_failure_preserves_output(mocker):
    run = mocker.patch(
        "tools.smoke_container.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "stdout", "stderr"),
    )
    with pytest.raises(RuntimeError, match="stdout\nstderr"):
        smoke_container.docker("inspect", "test")
    assert run.call_args.kwargs["timeout"] == 30


def test_failure_diagnoses_and_removes_both_containers(monkeypatch, tmp_path):
    command = Mock(return_value="")
    monkeypatch.setattr(smoke_container, "docker", command)
    monkeypatch.setattr(
        smoke_container, "wait_healthy", Mock(side_effect=RuntimeError("crashed"))
    )
    with pytest.raises(RuntimeError, match="crashed"):
        smoke_container.smoke("image", tmp_path)
    names = [
        call.args[2]
        for call in command.call_args_list
        if call.args[:2] == ("rm", "--force")
    ]
    assert len(names) == 2
    assert names[1] == f"{names[0]}-offline"
    for name in names:
        assert any(call.args == ("logs", name) for call in command.call_args_list)
        assert any(call.args == ("inspect", name) for call in command.call_args_list)


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("offline checks failed"), subprocess.TimeoutExpired("docker", 240)],
)
def test_offline_failure_cleans_up_and_propagates(monkeypatch, failure, tmp_path):
    def command(*args, **kwargs):
        if args[0] == "port":
            return "127.0.0.1:12345"
        if args[0] == "run" and "--network=none" in args:
            raise failure
        return ""

    mocked = Mock(side_effect=command)
    monkeypatch.setattr(smoke_container, "docker", mocked)
    monkeypatch.setattr(smoke_container, "wait_healthy", Mock())
    response = Mock(status=200)
    response.read.return_value = b'{"status": "ok"}'
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        "tools.smoke_container.urllib.request.urlopen", Mock(return_value=context)
    )
    with pytest.raises(type(failure)):
        smoke_container.smoke("image", tmp_path)
    assert (
        sum(call.args[:2] == ("rm", "--force") for call in mocked.call_args_list) == 2
    )


@pytest.mark.parametrize("failure", ["browser", "shutdown", "http"])
def test_late_failure_blocks_success_and_cleans_up(monkeypatch, tmp_path, failure):
    def command(*args, **kwargs):
        if args[0] == "port":
            return "127.0.0.1:12345"
        if args[:3] == ("inspect", "--format", "{{.State.ExitCode}}"):
            return "137" if failure == "shutdown" else "0"
        return ""

    mocked = Mock(side_effect=command)
    monkeypatch.setattr(smoke_container, "docker", mocked)
    monkeypatch.setattr(smoke_container, "wait_healthy", Mock())
    browser = Mock(
        side_effect=RuntimeError("browser failed") if failure == "browser" else None
    )
    monkeypatch.setattr(smoke_container, "check_browser", browser)
    response = Mock(status=200)
    response.read.return_value = (
        b'{"status": "down"}' if failure == "http" else b'{"status": "ok"}'
    )
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        "tools.smoke_container.urllib.request.urlopen", Mock(return_value=context)
    )
    with pytest.raises((AssertionError, RuntimeError)):
        smoke_container.smoke("image", tmp_path)
    assert (
        sum(call.args[:2] == ("rm", "--force") for call in mocked.call_args_list) == 2
    )
