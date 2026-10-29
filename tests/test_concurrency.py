"""Integration tests: many documents read at once, as the web API reads them.

The workspace reads every upload as soon as it's added, so a server parses
several documents on several threads at once, starting from a fresh process.
Shared state that only breaks under that load (pyparsing's parse-action
probing did, and then failed every later parse in the process) passes every
test that reads one document at a time. These reset the parser to the state
a new server starts in, then read real documents concurrently.
"""

import importlib
import json
import threading
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.bom_lib import BOM_PRESETS
from src.bom_lib.grammar import value_parser
from src.server import Settings, create_app

SAMPLES = Path(__file__).parent / "samples"
DOCUMENTS = [
    *sorted((SAMPLES / "dirty").glob("*.pdf")),
    *sorted((SAMPLES / "clean").glob("*.pdf")),
][:10]


@pytest.fixture
def fresh_server() -> Iterator[TestClient]:
    """An API whose parser hasn't parsed anything yet, as after a restart."""
    importlib.reload(value_parser)
    yield TestClient(create_app(Settings(requests_per_minute=10_000)))
    importlib.reload(value_parser)


def _all_at_once(jobs: list[Callable[[], Any]]) -> list[Any]:
    """Runs the jobs on their own threads, released together; their results."""
    barrier = threading.Barrier(len(jobs))
    results: list[Any] = [None] * len(jobs)

    def run(i: int) -> None:
        barrier.wait()
        try:
            results[i] = jobs[i]()
        except Exception as e:  # reported by the assertion
            results[i] = e

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(jobs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return results


def _reading(client: TestClient, path: Path) -> dict[str, Any]:
    response = client.post(
        "/api/documents/read",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return dict(events[-1])


def test_documents_read_at_once_all_read_cleanly(fresh_server: TestClient) -> None:
    assert len(DOCUMENTS) >= 6, "the test corpus is missing; run `just fetch-samples`"

    results = _all_at_once([partial(_reading, fresh_server, p) for p in DOCUMENTS])

    for path, result in zip(DOCUMENTS, results, strict=True):
        assert isinstance(result, dict), (path.name, result)
        assert result["event"] == "result", (path.name, result)
        assert result["reading"]["errors"] == [], (path.name, result)
        assert result["reading"]["parts_found"] > 0, path.name


def test_master_lists_built_at_once_all_succeed(fresh_server: TestClient) -> None:
    presets = list(BOM_PRESETS)[:8]

    def build(key: str) -> Any:
        request = json.dumps({"projects": [{"method": "preset", "preset": key}]})
        return fresh_server.post("/api/master-list", data={"request": request})

    results = _all_at_once([partial(build, k) for k in presets])

    for key, response in zip(presets, results, strict=True):
        assert response.status_code == 200, (key, response)
        assert response.json()["stats"]["errors"] == [], (key, response.json())
