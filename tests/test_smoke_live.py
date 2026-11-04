"""The live check's wait for a deploy, without reaching the network."""

import urllib.error
from unittest.mock import Mock

import pytest

from tools import smoke_live


def answer(body: bytes) -> Mock:
    response = Mock()
    response.read.return_value = body
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    return context


def test_reads_the_served_commit_past_the_cache(monkeypatch):
    urlopen = Mock(return_value=answer(b'{"commit": "abc"}'))
    monkeypatch.setattr("tools.smoke_live.urllib.request.urlopen", urlopen)
    assert smoke_live.served_commit("https://site", "abc") == "abc"
    assert urlopen.call_args.args[0] == "https://site/version.json?abc"


@pytest.mark.parametrize(
    "outcome",
    [
        urllib.error.URLError("refused"),
        TimeoutError("slow"),
        answer(b"<html>"),
        answer(b'["abc"]'),
        answer(b'{"commit": 1}'),
    ],
)
def test_an_unreadable_answer_is_reported_not_raised(monkeypatch, outcome):
    urlopen = (
        Mock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else Mock(return_value=outcome)
    )
    monkeypatch.setattr("tools.smoke_live.urllib.request.urlopen", urlopen)
    assert smoke_live.served_commit("https://site", "abc").startswith("unreadable")


def test_waits_for_the_new_commit(monkeypatch):
    served = Mock(side_effect=["old", "unreadable (refused)", "new"])
    sleep = Mock()
    monkeypatch.setattr(smoke_live, "served_commit", served)
    monkeypatch.setattr("tools.smoke_live.time.sleep", sleep)
    smoke_live.wait_for_commit("https://site", "new")
    assert served.call_count == 3
    assert sleep.call_count == 2


def test_names_the_stale_commit_at_the_deadline(monkeypatch):
    monkeypatch.setattr(smoke_live, "served_commit", Mock(return_value="old"))
    monkeypatch.setattr("tools.smoke_live.time.monotonic", Mock(side_effect=[0, 601]))
    with pytest.raises(TimeoutError, match="serves old, not new"):
        smoke_live.wait_for_commit("https://site", "new")
