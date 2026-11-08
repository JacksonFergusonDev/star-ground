"""Check the live site after a deploy: it serves this commit, and a parts list builds."""

import argparse
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.smoke_container import check_browser


def served_commit(site: str, commit: str) -> str:
    """Return the commit the site's ``version.json`` names, or why it couldn't be read.

    The query string asks the CDN for a copy it hasn't cached, so a deploy shows
    as soon as Pages serves it rather than when the cached copy expires.
    """
    try:
        with urllib.request.urlopen(
            f"{site}/version.json?{commit}", timeout=10
        ) as response:
            served = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return f"unreadable ({error})"
    if isinstance(served, dict) and isinstance(served.get("commit"), str):
        return str(served["commit"])
    return f"unreadable ({served!r})"


def wait_for_commit(site: str, commit: str, timeout: float = 600) -> None:
    """Wait until the site serves ``commit``, failing with what it serves instead."""
    deadline = time.monotonic() + timeout
    while (served := served_commit(site, commit)) != commit:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{site} serves {served}, not {commit}, after {timeout:.0f}s"
            )
        time.sleep(10)
    print(f"{site} serves {commit}", flush=True)


def main() -> None:
    """Wait for the deployed commit, then drive the live site through a parts list."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", help="The site's address, such as https://example.com")
    parser.add_argument("commit", help="The commit the deploy published")
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="Keep browser traces and screenshots here",
    )
    args = parser.parse_args()
    site = args.site.rstrip("/")
    wait_for_commit(site, args.commit)
    with tempfile.TemporaryDirectory(prefix="star-ground-live-") as temporary:
        check_browser(site, args.artifacts or Path(temporary))
    print("The live site built a shopping list from a pasted parts list", flush=True)


if __name__ == "__main__":
    main()
