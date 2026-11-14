"""Writes the shopping list the website's snapshot page shows.

`web/src/pages/embed/shopping-list.astro` draws the app's shopping list as
plain HTML, for other sites to show as a picture of the app. Its parts are the
Big Muff π's, from the netlist the hero draws (as the example build pack's
are), and this script asks the API for their master list, so the page shows
what the app would. The file is committed, and a test fails when it no longer
matches the API. Run `just snapshot` after a change that moves it.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.example_pack import NETLIST, parts_list
from star_ground.cli_output import CliOutput
from star_ground.server import Settings, create_app

SNAPSHOT_FILE = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "src"
    / "data"
    / "shopping-list-snapshot.json"
)


def snapshot_text() -> str:
    """The Big Muff π's master list, formatted as committed.

    Returns:
        The API's `MasterListResult` as indented JSON with a trailing newline.

    Raises:
        RuntimeError: The API refused the request or couldn't read the parts.
    """
    request = {
        "projects": [
            {
                "method": "paste",
                "name": "Big Muff π",
                "text": parts_list(NETLIST.read_text(encoding="utf-8")),
            }
        ]
    }
    client = TestClient(create_app(Settings()))
    response = client.post("/api/master-list", data={"request": json.dumps(request)})
    if response.status_code != 200:
        raise RuntimeError(f"The API answered {response.status_code}: {response.text}")
    result = response.json()
    if any(project["errors"] for project in result["projects"]):
        raise RuntimeError("The API couldn't read the Big Muff π's parts.")
    return json.dumps(result, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    """Writes the shopping list to `web/src/data/`."""
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(snapshot_text(), encoding="utf-8")
    CliOutput().success(f"Wrote {SNAPSHOT_FILE.name}")


if __name__ == "__main__":
    main()
