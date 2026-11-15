"""Writes the projects the website's snapshot page shows.

`web/src/pages/embed/projects.astro` draws the app's projects page, sidebar
and all, as plain HTML, for other sites to show as a picture of the app. Its
projects are two catalog builds, and this script reads their documents and
asks for their master list through the API, as the app does when they're
added, so the page shows what the app would. The file is committed. Reading the
documents fetches them from their publisher, so the test that it's current is
a network test (`just test-network`); run `just snapshot` after a change that
moves it.
"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from star_ground.cli_output import CliOutput
from star_ground.server import Settings, create_app

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_FILE = ROOT / "web" / "src" / "data" / "projects-snapshot.json"
# The catalog builds on the page, as `<publisher>/<id>` under `catalog/builds/`.
BUILDS = ("pedalpcb/aberrationfuzz-pedalpcb", "pedalpcb/angrycharles")


def _reading(client: TestClient, url: str) -> dict[str, Any]:
    """A linked document's reading, from the API's stream of events."""
    response = client.post("/api/documents/read", data={"url": url})
    if response.status_code != 200:
        raise RuntimeError(f"The API answered {response.status_code}: {response.text}")
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line.removeprefix("data: "))
        if event["event"] == "result":
            reading: dict[str, Any] = event["reading"]
            return reading
        if event["event"] == "error":
            raise RuntimeError(f"The API couldn't read {url}: {event}")
    raise RuntimeError(f"The API sent no reading for {url}.")


def snapshot_text() -> str:
    """The catalog builds' readings and master list, formatted as committed.

    Returns:
        Each build's catalog id, title, link and `DocumentReading`, and their
        `MasterListResult`, as indented JSON with a trailing newline.

    Raises:
        RuntimeError: The API refused a request or couldn't read the parts.
    """
    client = TestClient(create_app(Settings()))
    builds = []
    for build in BUILDS:
        entry = json.loads(
            (ROOT / "catalog" / "builds" / f"{build}.json").read_text("utf-8")
        )
        builds.append(
            {
                "catalog": build,
                "title": entry["title"],
                "url": entry["document"],
                "reading": _reading(client, entry["document"]),
            }
        )
    request = {
        "show_hardware": True,
        "show_extras": True,
        "projects": [
            {"method": "url", "name": b["title"], "url": b["url"], "count": 1}
            for b in builds
        ],
    }
    response = client.post("/api/master-list", data={"request": json.dumps(request)})
    if response.status_code != 200:
        raise RuntimeError(f"The API answered {response.status_code}: {response.text}")
    result = response.json()
    if any(project["errors"] for project in result["projects"]):
        raise RuntimeError("The API couldn't read a build's parts.")
    snapshot = {"builds": builds, "result": result}
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    """Writes the projects to `web/src/data/`."""
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(snapshot_text(), encoding="utf-8")
    CliOutput().success(f"Wrote {SNAPSHOT_FILE.name}")


if __name__ == "__main__":
    main()
