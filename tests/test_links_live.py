"""Every external link the website points builders to still leads somewhere.

These reach the internet, so they are deselected by default (`-m "not
network"` in pyproject.toml). `.github/workflows/links.yml` runs them weekly
and on pull requests that change the website: `uv run pytest -m network
tests/test_links_live.py`.

A link fails only on a sure sign it's dead: a 404 or 410, a server error, a
host that no longer answers, or a YouTube video that's gone. Some sites
(Cloudflare-fronted forums, LinkedIn) refuse any request that isn't a browser;
those count as alive, with a warning, since the check never pretends to be one.
"""

import re
import warnings
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

pytestmark = pytest.mark.network

WEB_SRC = Path(__file__).parent.parent / "web" / "src"
# Hand-written links only: data files hold catalog and supplier URLs, which
# the catalog job checks as it reads them.
SOURCES = ("*.svelte", "*.astro", "*.ts")
SKIPPED_DIRS = {"data"}
URL = re.compile(r"https://[^\s\"'`<>(){}\[\]…]+")
# The site answered, but turns away anything that isn't a browser.
REFUSES_ROBOTS = {401, 403, 429, 999}
DEAD = {404, 410}
HEADERS = {
    "User-Agent": "star-ground-link-check (+https://github.com/JacksonFergusonDev/star-ground)"
}
TIMEOUT = 20


def _links() -> list[str]:
    found: set[str] = set()
    for pattern in SOURCES:
        for path in WEB_SRC.rglob(pattern):
            if SKIPPED_DIRS & set(path.relative_to(WEB_SRC).parts):
                continue
            text = path.read_text(encoding="utf-8")
            for match in URL.finditer(text):
                # A URL that trails off ("https://docs.pedalpcb.com/…") is an example.
                if not text.startswith("…", match.end()):
                    found.add(match.group().rstrip(".,;:"))
    return sorted(found)


def _youtube_video(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.netloc.endswith("youtube.com") and parts.path == "/watch":
        return parse_qs(parts.query).get("v", [None])[0]
    if parts.netloc == "youtu.be":
        return parts.path.lstrip("/") or None
    return None


def _status(url: str) -> int:
    video = _youtube_video(url)
    if video is not None:
        # A removed video's page still answers 200; oEmbed answers 404.
        url = f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={video}"
    for attempt in range(2):
        try:
            with requests.get(
                url, headers=HEADERS, timeout=TIMEOUT, stream=True
            ) as response:
                if response.status_code < 500 or attempt:
                    return response.status_code
        except requests.ConnectionError:
            if attempt:
                raise
    raise AssertionError("unreachable")


def test_the_website_has_external_links() -> None:
    assert "https://www.youtube.com/watch?v=WbDSaliGZeo" in _links()


@pytest.mark.parametrize("url", _links())
def test_each_external_link_is_alive(url: str) -> None:
    status = _status(url)
    if status in REFUSES_ROBOTS:
        warnings.warn(
            f"{url} refused the check ({status}); open it by hand", stacklevel=1
        )
        return
    assert status not in DEAD, f"{url} answered {status}"
    assert status < 500, f"{url} answered {status}"
