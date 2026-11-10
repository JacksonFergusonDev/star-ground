"""Tests for the catalog job (`star_ground.catalog`), with a fake web and fake readers."""

import hashlib
import io
import json
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import requests
from rich.console import Console

from star_ground.bom_lib import (
    ComponentCategory,
    FileUpload,
    ParseResult,
    ParseWarning,
    ParseWarningKind,
)
from star_ground.bom_lib.types import (
    Inventory,
    Location,
    ReadingStats,
    make_component_key,
)
from star_ground.catalog.build import (
    BOARDS_VERSION,
    Budget,
    CatalogRun,
    Sample,
    State,
    clean,
    entry_id,
    entry_title,
)
from star_ground.catalog.client import (
    BACKOFF_S,
    MAX_REDIRECTS,
    MAX_ROBOTS_BYTES,
    MAX_WAIT_S,
    MIN_DELAY_S,
    RETRIES,
    BlockedError,
    DisallowedError,
    GoneError,
    PoliteClient,
)
from star_ground.catalog.discover import (
    PAGE_VERSION,
    Discovery,
    Listing,
    PageDetails,
    PageState,
    discover,
    page_details,
    pdf_links,
)
from star_ground.catalog.effects import (
    Effect,
    Overrides,
    effects,
    from_categories,
    from_title,
)
from star_ground.catalog.entries import CatalogEntry
from star_ground.catalog.report import Event, Outcome, Reporter
from star_ground.catalog.sources import SOURCES, Publisher
from star_ground.catalog.verified import (
    Checked,
    Verification,
    VerifiedList,
    document_hash,
    fingerprint,
)


@dataclass
class _Response:
    status_code: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    cut_short: bool = False
    closed: bool = False
    read: int = 0

    def iter_content(self, size: int) -> Iterator[bytes]:
        for start in range(0, len(self.body), size):
            self.read = start + size
            yield self.body[start : start + size]
        if self.cut_short:
            raise requests.exceptions.ChunkedEncodingError("length mismatch")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _Web:
    """A fake session: answers by URL, in turn when a URL has several answers."""

    def __init__(
        self, pages: Mapping[str, _Response | list[_Response | Exception]]
    ) -> None:
        self.headers: dict[str, str] = {}
        self._pages: dict[str, list[_Response | Exception]] = {
            k: v if isinstance(v, list) else [v] for k, v in pages.items()
        }
        self.asked: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str], **_: Any) -> _Response:
        self.asked.append((url, headers))
        if url.endswith("/robots.txt") and url not in self._pages:
            return _Response(404)
        answers = self._pages.get(url) or [_Response(404)]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def urls(self) -> list[str]:
        return [u for u, _ in self.asked if not u.endswith("/robots.txt")]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _on_test_sites(url: str) -> bool:
    """The publisher's site, for the client's tests: any host under `.test`."""
    return (urlsplit(url).hostname or "").endswith(".test")


def _client(web: _Web, clock: _Clock | None = None) -> PoliteClient:
    clock = clock or _Clock()
    return PoliteClient(web, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]


# --- The polite client ---


def test_every_request_says_it_is_star_ground() -> None:
    web = _Web({"https://a.test/x": _Response(body=b"hi")})

    _client(web).get("https://a.test/x", allows=_on_test_sites)

    assert web.headers["User-Agent"].startswith("StarGround/")
    assert "star-ground.jacksonferguson.me" in web.headers["User-Agent"]


def test_a_url_robots_txt_disallows_is_not_requested() -> None:
    web = _Web(
        {
            "https://a.test/robots.txt": _Response(
                body=b"User-agent: *\nDisallow: /private/"
            )
        }
    )

    with pytest.raises(DisallowedError):
        _client(web).get("https://a.test/private/doc.pdf", allows=_on_test_sites)

    assert web.urls() == []


def test_requests_to_a_site_are_spaced_by_its_crawl_delay() -> None:
    web = _Web(
        {
            "https://a.test/robots.txt": _Response(
                body=b"User-agent: *\nCrawl-delay: 30"
            ),
            "https://a.test/1": _Response(),
            "https://a.test/2": _Response(),
        }
    )
    clock = _Clock()
    client = _client(web, clock)

    client.get("https://a.test/1", allows=_on_test_sites)
    client.get("https://a.test/2", allows=_on_test_sites)

    # robots.txt, then each page: never closer than the larger delay.
    assert len(clock.slept) == 2
    assert all(s >= 30 for s in clock.slept)


def test_a_companion_client_s_requests_are_counted() -> None:
    web = _Web({"https://a.test/1": _Response(), "https://b.test/1": _Response()})
    client = _client(web)

    client.get("https://a.test/1", allows=_on_test_sites)
    client.alongside().get("https://b.test/1", allows=_on_test_sites)

    # Each host's robots.txt, then its page.
    assert client.request_count == 4


def test_requests_wait_at_least_the_minimum() -> None:
    web = _Web({"https://a.test/1": _Response()})
    clock = _Clock()

    _client(web, clock).get("https://a.test/1", allows=_on_test_sites)

    assert clock.slept
    assert clock.slept[0] >= MIN_DELAY_S


def test_a_bot_check_stops_the_site_for_the_run() -> None:
    web = _Web(
        {
            "https://a.test/1": _Response(503, headers={"cf-mitigated": "challenge"}),
            "https://a.test/2": _Response(),
        }
    )
    client = _client(web)

    with pytest.raises(BlockedError, match="bot check"):
        client.get("https://a.test/1", allows=_on_test_sites)
    with pytest.raises(BlockedError):
        client.get("https://a.test/2", allows=_on_test_sites)

    assert web.urls() == ["https://a.test/1"]


def test_too_many_requests_is_waited_out() -> None:
    web = _Web(
        {
            "https://a.test/1": [
                _Response(429, headers={"Retry-After": "120"}),
                _Response(body=b"ok"),
            ]
        }
    )
    clock = _Clock()

    fetched = _client(web, clock).get("https://a.test/1", allows=_on_test_sites)

    assert fetched.content == b"ok"
    assert 120 in clock.slept


def test_a_timeout_is_waited_out() -> None:
    web = _Web({"https://a.test/1": [requests.ReadTimeout(), _Response(body=b"ok")]})
    clock = _Clock()

    fetched = _client(web, clock).get("https://a.test/1", allows=_on_test_sites)

    assert fetched.content == b"ok"
    assert BACKOFF_S in clock.slept


def test_a_site_that_never_answers_fails_after_its_retries() -> None:
    web = _Web({"https://a.test/1": [requests.ConnectTimeout()]})

    with pytest.raises(ConnectionError, match="didn't answer"):
        _client(web).get("https://a.test/1", allows=_on_test_sites)
    assert web.urls() == ["https://a.test/1"] * (RETRIES + 1)


def test_too_many_requests_that_persists_stops_the_site() -> None:
    web = _Web({"https://a.test/1": _Response(429)})

    with pytest.raises(BlockedError, match="429"):
        _client(web).get("https://a.test/1", allows=_on_test_sites)


def test_an_unchanged_document_costs_a_304() -> None:
    web = _Web({"https://a.test/d.pdf": _Response(304)})

    fetched = _client(web).get(
        "https://a.test/d.pdf", etag='"v1"', last_modified="Mon", allows=_on_test_sites
    )

    assert fetched.status == 304
    assert fetched.content == b""
    assert web.asked[-1][1] == {"If-None-Match": '"v1"', "If-Modified-Since": "Mon"}


def test_a_pdf_cut_short_at_its_end_marker_is_whole() -> None:
    web = _Web(
        {
            "https://a.test/d.pdf": _Response(
                body=b"%PDF-1.4 ... %%EOF\n", cut_short=True
            )
        }
    )

    assert (
        _client(web)
        .get("https://a.test/d.pdf", allows=_on_test_sites)
        .content.endswith(b"%%EOF\n")
    )


def test_a_pdf_cut_short_before_its_end_is_a_failure() -> None:
    web = _Web(
        {"https://a.test/d.pdf": _Response(body=b"%PDF-1.4 ...", cut_short=True)}
    )

    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        _client(web).get("https://a.test/d.pdf", allows=_on_test_sites)


def _moved(to: str) -> _Response:
    return _Response(301, headers={"Location": to})


def test_a_redirect_within_the_site_is_followed() -> None:
    web = _Web(
        {
            "https://a.test/old.pdf": _moved("https://docs.a.test/new.pdf"),
            "https://docs.a.test/new.pdf": _Response(body=b"%PDF"),
        }
    )

    fetched = _client(web).get("https://a.test/old.pdf", allows=_on_test_sites)

    assert fetched.content == b"%PDF"
    # The new host's robots.txt is read before it's asked.
    assert [u for u, _ in web.asked] == [
        "https://a.test/robots.txt",
        "https://a.test/old.pdf",
        "https://docs.a.test/robots.txt",
        "https://docs.a.test/new.pdf",
    ]


def test_a_redirect_off_the_site_is_not_followed() -> None:
    web = _Web({"https://a.test/d.pdf": _moved("https://elsewhere.example/d.pdf")})

    with pytest.raises(DisallowedError, match="isn't on the publisher's site"):
        _client(web).get("https://a.test/d.pdf", allows=_on_test_sites)

    assert not any("elsewhere" in u for u, _ in web.asked)


def test_a_redirect_robots_txt_disallows_is_not_followed() -> None:
    web = _Web(
        {
            "https://a.test/d.pdf": _moved("/private/d.pdf"),
            "https://a.test/robots.txt": _Response(
                body=b"User-agent: *\nDisallow: /private/"
            ),
        }
    )

    with pytest.raises(DisallowedError, match=r"robots\.txt disallows"):
        _client(web).get("https://a.test/d.pdf", allows=_on_test_sites)

    assert web.urls() == ["https://a.test/d.pdf"]


def test_each_redirect_waits_its_turn() -> None:
    web = _Web(
        {
            "https://a.test/1": _moved("/2"),
            "https://a.test/2": _Response(body=b"ok"),
        }
    )
    clock = _Clock()

    _client(web, clock).get("https://a.test/1", allows=_on_test_sites)

    # robots.txt, then /1, then /2: each after the last by the minimum.
    assert len(clock.slept) == 2
    assert all(s >= MIN_DELAY_S for s in clock.slept)


def test_redirects_are_followed_only_so_far() -> None:
    web = _Web({f"https://a.test/{n}": _moved(f"/{n + 1}") for n in range(10)})

    with pytest.raises(requests.exceptions.TooManyRedirects):
        _client(web).get("https://a.test/0", allows=_on_test_sites)

    assert len(web.urls()) == MAX_REDIRECTS + 1


def test_a_robots_txt_that_cannot_be_read_stops_the_host() -> None:
    web = _Web(
        {"https://a.test/robots.txt": _Response(503), "https://a.test/1": _Response()}
    )
    client = _client(web)

    with pytest.raises(BlockedError, match="503"):
        client.get("https://a.test/1", allows=_on_test_sites)
    with pytest.raises(BlockedError):
        client.get("https://a.test/2", allows=_on_test_sites)

    # Tried again after waiting, as any server error is; never the page.
    assert [u for u, _ in web.asked] == ["https://a.test/robots.txt"] * (RETRIES + 1)


def test_a_robots_txt_that_never_answers_stops_the_host() -> None:
    web = _Web({"https://a.test/robots.txt": [requests.ConnectTimeout()]})

    with pytest.raises(BlockedError, match="didn't answer"):
        _client(web).get("https://a.test/1", allows=_on_test_sites)

    assert web.urls() == []


def test_a_missing_robots_txt_allows_everything() -> None:
    web = _Web(
        {"https://a.test/robots.txt": _Response(410), "https://a.test/1": _Response()}
    )

    assert _client(web).get("https://a.test/1", allows=_on_test_sites).status == 200


def test_a_robots_txt_is_read_only_so_far_and_closed() -> None:
    padding = b"# " + b"x" * 2 * MAX_ROBOTS_BYTES + b"\n"
    robots = _Response(
        body=b"User-agent: *\nDisallow: /a/\n" + padding + b"Disallow: /b/\n"
    )
    web = _Web({"https://a.test/robots.txt": robots, "https://a.test/b/1": _Response()})
    client = _client(web)

    with pytest.raises(DisallowedError):
        client.get("https://a.test/a/1", allows=_on_test_sites)
    # A rule past the first 500 KiB isn't read.
    assert client.get("https://a.test/b/1", allows=_on_test_sites).status == 200
    assert robots.read < len(robots.body)
    assert robots.closed


def test_a_retry_after_too_long_to_wait_stops_the_host() -> None:
    web = _Web(
        {
            "https://a.test/1": _Response(429, headers={"Retry-After": "864000"}),
            "https://a.test/2": _Response(),
        }
    )
    clock = _Clock()
    client = _client(web, clock)

    with pytest.raises(BlockedError, match="864000"):
        client.get("https://a.test/1", allows=_on_test_sites)
    with pytest.raises(BlockedError):
        client.get("https://a.test/2", allows=_on_test_sites)

    assert max(clock.slept) <= MAX_WAIT_S


def test_a_companion_client_waits_its_turn_at_a_shared_host() -> None:
    web = _Web({"https://a.test/1": _Response(), "https://a.test/2": _Response()})
    clock = _Clock()
    client = _client(web, clock)

    client.get("https://a.test/1", allows=_on_test_sites)
    clock.slept.clear()
    client.alongside().get("https://a.test/2", allows=_on_test_sites)

    # robots.txt is read once for both, and the second request waits.
    assert [u for u, _ in web.asked] == [
        "https://a.test/robots.txt",
        "https://a.test/1",
        "https://a.test/2",
    ]
    assert len(clock.slept) == 1
    assert clock.slept[0] >= MIN_DELAY_S


def test_a_host_that_turns_one_client_away_is_skipped_by_its_companion() -> None:
    web = _Web(
        {
            "https://a.test/1": _Response(403),
            "https://a.test/2": _Response(),
        }
    )
    client = _client(web)

    with pytest.raises(BlockedError):
        client.alongside().get("https://a.test/1", allows=_on_test_sites)
    with pytest.raises(BlockedError, match="earlier in this run"):
        client.get("https://a.test/2", allows=_on_test_sites)


def test_clients_alongside_each_other_take_turns_at_a_host() -> None:
    # Two threads at once: neither request goes before the other's turn ends.
    web = _Web({f"https://a.test/{n}": _Response() for n in range(2)})
    lock = threading.Lock()
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        with lock:
            slept.append(seconds)

    client = PoliteClient(web, sleep=sleep, clock=lambda: 0.0)  # type: ignore[arg-type]
    client.get("https://a.test/0", allows=_on_test_sites)
    slept.clear()
    others = [client.alongside() for _ in range(2)]
    threads = [
        threading.Thread(
            target=lambda c=c, n=n: c.get(f"https://a.test/{n}", allows=_on_test_sites)
        )
        for n, c in enumerate(others)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # With the clock stopped, each turn is a whole delay after the one before.
    first, second = sorted(slept)
    assert first >= MIN_DELAY_S
    assert second >= first + MIN_DELAY_S


# --- Discovery ---


def test_a_product_page_link_must_say_it_is_a_build_document() -> None:
    source = SOURCES[Publisher.GUITARPCB]
    html = """
      <a href="/wp-content/uploads/Fuzz-Build-Document.pdf">Fuzz Build Doc</a>
      <a href="/wp-content/uploads/Fuzz-Reference.pdf">Reference guide</a>
      <a href="/wp-content/uploads/Fuzz-Artwork.pdf">Build doc artwork</a>
      <a href="https://elsewhere.test/Other.pdf">Build doc</a>
    """

    links = pdf_links("https://guitarpcb.com/product/fuzz/", html, source)

    assert [li.name for li in links] == ["Fuzz-Build-Document.pdf"]


def test_a_table_row_heading_can_mark_a_build_document() -> None:
    source = SOURCES[Publisher.FIVECATS]
    html = """<table><tr><th>Build Docs</th>
      <td><a href="/wp-content/uploads/FCP-Fuzz-Insert.pdf">Download</a></td></tr></table>"""

    links = pdf_links("https://www.five-cats-pedals.co.uk/product/fuzz/", html, source)

    assert [li.name for li in links] == ["FCP-Fuzz-Insert.pdf"]


def test_pedalpcb_datasheets_are_not_build_documents() -> None:
    source = SOURCES[Publisher.PEDALPCB]
    html = """
      <a href="https://docs.pedalpcb.com/project/Fuzz.pdf">Build Docs</a>
      <a href="https://docs.pedalpcb.com/datasheet/bat42.pdf">BAT42</a>
    """

    links = pdf_links("https://www.pedalpcb.com/product/fuzz/", html, source)

    assert [li.name for li in links] == ["Fuzz.pdf"]


def test_pedalpcb_build_documents_are_under_its_project_paths() -> None:
    source = SOURCES[Publisher.PEDALPCB]

    assert source.is_build("https://docs.pedalpcb.com/project/Fuzz.pdf")
    assert source.is_build("https://www.pedalpcb.com/docs/Byzantium-PartsList.pdf")
    assert not source.is_build("https://docs.pedalpcb.com/Wiring.pdf")
    assert not source.is_build(
        "https://docs.pedalpcb.com/project/PedalPCB-Auditorium.pdf"
    )


_SITEMAP_INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.pedalpcb.com/product-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://www.pedalpcb.com/post-sitemap.xml</loc></sitemap>
</sitemapindex>"""
_PRODUCTS = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.pedalpcb.com/product/fuzz/</loc><lastmod>2026-01-01</lastmod></url>
  <url><loc>https://www.pedalpcb.com/product/drive/</loc><lastmod>2026-02-02</lastmod></url>
</urlset>"""


def _pedalpcb_web() -> _Web:
    return _Web(
        {
            "https://www.pedalpcb.com/sitemap_index.xml": _Response(
                body=_SITEMAP_INDEX
            ),
            "https://www.pedalpcb.com/product-sitemap.xml": _Response(body=_PRODUCTS),
            "https://www.pedalpcb.com/product/fuzz/": _Response(
                body=b'<a href="https://docs.pedalpcb.com/project/Fuzz.pdf">Build Docs</a>'
            ),
            "https://www.pedalpcb.com/product/drive/": _Response(
                body=b'<a href="https://docs.pedalpcb.com/project/Drive.pdf">Build Docs</a>'
            ),
        }
    )


def test_a_sitemap_that_comes_back_as_another_page_is_asked_for_again() -> None:
    web = _pedalpcb_web()
    index = "https://www.pedalpcb.com/sitemap_index.xml"
    web._pages[index] = [
        _Response(body=b"<html><body><p>Down for maintenance</body></html>"),
        _Response(body=_SITEMAP_INDEX),
    ]
    clock = _Clock()

    found = discover(_client(web, clock), SOURCES[Publisher.PEDALPCB])

    assert "https://docs.pedalpcb.com/project/Fuzz.pdf" in found.listings
    assert BACKOFF_S in clock.slept


def test_a_sitemap_that_never_comes_back_fails_after_its_retries() -> None:
    web = _pedalpcb_web()
    web._pages["https://www.pedalpcb.com/sitemap_index.xml"] = [
        _Response(body=b"<html><body><p>Down for maintenance</body></html>")
    ] * (RETRIES + 1)

    with pytest.raises(ValueError, match="didn't answer with a sitemap in 4 tries"):
        discover(_client(web), SOURCES[Publisher.PEDALPCB])


def test_a_sitemap_that_never_comes_back_is_named_by_its_title() -> None:
    web = _pedalpcb_web()
    web._pages["https://www.pedalpcb.com/sitemap_index.xml"] = [
        _Response(body=b"<html><head><title> Just a moment </title></head><body><p>")
    ] * (RETRIES + 1)

    with pytest.raises(ValueError, match="a page titled 'Just a moment'"):
        discover(_client(web), SOURCES[Publisher.PEDALPCB])


def test_discovery_walks_only_product_sitemaps() -> None:
    web = _pedalpcb_web()

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB])

    assert sorted(found.listings) == [
        "https://docs.pedalpcb.com/project/Drive.pdf",
        "https://docs.pedalpcb.com/project/Fuzz.pdf",
    ]
    assert "https://www.pedalpcb.com/post-sitemap.xml" not in web.urls()


def test_a_page_unchanged_since_the_last_run_is_not_fetched() -> None:
    web = _pedalpcb_web()
    fuzz = Listing(
        "https://docs.pedalpcb.com/project/Fuzz.pdf",
        "https://www.pedalpcb.com/product/fuzz/",
        "Fuzz.pdf",
    )
    known = {"https://www.pedalpcb.com/product/fuzz/": PageState("2026-01-01", (fuzz,))}

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], known)

    assert "https://www.pedalpcb.com/product/fuzz/" not in web.urls()
    assert fuzz.document in found.listings


def test_a_page_listed_twice_takes_its_newer_date() -> None:
    web = _pedalpcb_web()
    fuzz = "https://www.pedalpcb.com/product/fuzz/"
    web._pages["https://www.pedalpcb.com/product-sitemap.xml"] = [
        _Response(
            body=_PRODUCTS.replace(
                b"</urlset>",
                f"<url><loc>{fuzz}</loc><lastmod>2025-06-01</lastmod></url></urlset>".encode(),
            )
        )
    ]

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB])

    assert found.pages[fuzz].lastmod == "2026-01-01"


def test_a_page_listed_with_an_older_date_is_not_fetched() -> None:
    web = _pedalpcb_web()
    fuzz = Listing(
        "https://docs.pedalpcb.com/project/Fuzz.pdf",
        "https://www.pedalpcb.com/product/fuzz/",
        "Fuzz.pdf",
    )
    page = "https://www.pedalpcb.com/product/fuzz/"
    known = {page: PageState("2026-05-05T10:00:00+00:00", (fuzz,))}

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], known)

    assert page not in web.urls()
    assert found.pages[page].lastmod == "2026-05-05T10:00:00+00:00"
    assert fuzz.document in found.listings


def test_a_page_that_fails_is_reported_and_the_rest_go_on() -> None:
    web = _pedalpcb_web()
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [_Response(500)] * 5

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB])

    assert "https://www.pedalpcb.com/product/fuzz/" in found.failures
    assert list(found.listings) == ["https://docs.pedalpcb.com/project/Drive.pdf"]


def _unresolved() -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError(
        "NameResolutionError: Failed to resolve 'docs.pedalpcb.com'"
    )


def test_a_name_that_does_not_resolve_is_waited_out_not_gone() -> None:
    # A resolver blip on the runner looks just like a host that's gone.
    web = _Web({"https://a.test/d.pdf": [_unresolved(), _Response(body=b"%PDF")]})
    clock = _Clock()

    fetched = _client(web, clock).get("https://a.test/d.pdf", allows=_on_test_sites)

    assert fetched.content == b"%PDF"
    assert BACKOFF_S in clock.slept


def test_a_name_that_never_resolves_fails_after_its_retries() -> None:
    web = _Web({"https://a.test/d.pdf": [_unresolved()]})

    with pytest.raises(ConnectionError, match="didn't answer"):
        _client(web).get("https://a.test/d.pdf", allows=_on_test_sites)


def test_a_missing_document_is_gone() -> None:
    web = _Web({"https://a.test/d.pdf": _Response(404)})

    with pytest.raises(GoneError, match="404"):
        _client(web).get("https://a.test/d.pdf", allows=_on_test_sites)


def test_a_sample_spreads_across_the_pages() -> None:
    web = _pedalpcb_web()

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], limit=1)

    assert len(found.pages) == 1


def test_a_sample_larger_than_the_pages_reads_each_once() -> None:
    web = _pedalpcb_web()

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], limit=5)

    products = [u for u in web.urls() if "/product/" in u]
    assert sorted(products) == [
        "https://www.pedalpcb.com/product/drive/",
        "https://www.pedalpcb.com/product/fuzz/",
    ]
    assert len(found.pages) == 2


def test_a_malformed_link_is_skipped_not_the_page_or_the_run() -> None:
    web = _pedalpcb_web()
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [
        _Response(
            body=b'<a href="http://[oops/x.pdf">Build Docs</a>'
            b'<a href="https://docs.pedalpcb.com/project/Fuzz.pdf">Build Docs</a>'
        )
    ]

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB])

    assert sorted(found.listings) == [
        "https://docs.pedalpcb.com/project/Drive.pdf",
        "https://docs.pedalpcb.com/project/Fuzz.pdf",
    ]
    assert not found.failures


def _tonepad_web(file_info: _Response) -> _Web:
    return _Web(
        {
            "https://tonepad.com/projects.asp?projectType=fx": _Response(
                body=b'<a href="project.asp?id=7">Fuzz</a>'
                b'<a href="project.asp?id=8">Wah</a>'
            ),
            "https://tonepad.com/projects.asp?projectType=amp": _Response(body=b""),
            "https://tonepad.com/project.asp?id=7": _Response(
                body=b'<a href="getFileInfo.asp?id=81">fuzz_layout.pdf</a>'
            ),
            "https://tonepad.com/getFileInfo.asp?id=81": file_info,
            "https://tonepad.com/project.asp?id=8": _Response(
                body=b'<a href="getFileInfo.asp?id=91">wah_layout.pdf</a>'
            ),
            "https://tonepad.com/getFileInfo.asp?id=91": _Response(
                body=b'<a href="getFile.asp?id=91">Download</a>'
            ),
        }
    )


_FUZZ_LAYOUT = Listing(
    "https://tonepad.com/getFile.asp?id=81",
    "https://tonepad.com/project.asp?id=7",
    "fuzz_layout.pdf",
)


def test_a_tonepad_file_page_that_fails_keeps_its_project_s_documents() -> None:
    web = _tonepad_web(_Response(500))
    known = {_FUZZ_LAYOUT.page: PageState(None, (_FUZZ_LAYOUT,))}

    found = discover(_client(web), SOURCES[Publisher.TONEPAD], known)

    assert _FUZZ_LAYOUT.page in found.failures
    # The other project is still read, and this one keeps what it linked.
    assert sorted(found.listings) == [
        "https://tonepad.com/getFile.asp?id=81",
        "https://tonepad.com/getFile.asp?id=91",
    ]


def test_a_tonepad_file_page_that_is_gone_drops_only_its_document() -> None:
    web = _tonepad_web(_Response(404))
    known = {_FUZZ_LAYOUT.page: PageState(None, (_FUZZ_LAYOUT,))}

    found = discover(_client(web), SOURCES[Publisher.TONEPAD], known)

    assert not found.failures
    assert found.pages[_FUZZ_LAYOUT.page].listings == ()
    assert list(found.listings) == ["https://tonepad.com/getFile.asp?id=91"]


def test_a_tonepad_file_page_behind_a_bot_check_stops_the_host() -> None:
    web = _tonepad_web(_Response(403))

    found = discover(_client(web), SOURCES[Publisher.TONEPAD])

    assert "bot check" in found.failures[_FUZZ_LAYOUT.page]
    # The next project isn't asked for.
    assert "https://tonepad.com/project.asp?id=8" not in web.urls()
    assert not found.listings


def test_tonepad_documents_come_from_published_file_pages() -> None:
    source = SOURCES[Publisher.TONEPAD]
    web = _Web(
        {
            "https://tonepad.com/projects.asp?projectType=fx": _Response(
                body=b'<a href="project.asp?id=7">Fuzz</a>'
            ),
            "https://tonepad.com/projects.asp?projectType=amp": _Response(body=b""),
            "https://tonepad.com/project.asp?id=7": _Response(
                body=b'<a href="getFileInfo.asp?id=81">fuzz_layout.pdf</a>'
                b'<a href="getFileInfo.asp?id=82">fuzz_schematic.pdf</a>'
            ),
            "https://tonepad.com/getFileInfo.asp?id=81": _Response(
                body=b'<a href="getFile.asp?id=81">Download</a>'
            ),
        }
    )

    found = discover(_client(web), source)

    assert list(found.listings.values()) == [
        Listing(
            "https://tonepad.com/getFile.asp?id=81",
            "https://tonepad.com/project.asp?id=7",
            "fuzz_layout.pdf",
        )
    ]


# --- Reading and keeping ---


def _reading(parts: int = 2, warnings: list[ParseWarning] | None = None) -> ParseResult:
    inventory = Inventory()
    for n in range(1, parts + 1):
        inventory.add_part(
            "doc", make_component_key(ComponentCategory.RESISTORS, "10k"), f"R{n}"
        )
    stats = ReadingStats()
    stats.warnings = warnings or []
    return ParseResult(inventory=inventory, stats=stats, title="Fuzz")


def test_a_clean_reading_has_parts_and_no_doubts() -> None:
    assert clean(_reading()) is None
    assert clean(_reading(parts=0)) == "no parts were read"
    doubt = ParseWarning(ParseWarningKind.UNPARSED_VALUE, "R1", "Check its value.")
    assert "self-check" in str(clean(_reading(warnings=[doubt])))
    note = ParseWarning(ParseWarningKind.FOOTNOTE, "R1", "See the footnote.")
    assert clean(_reading(warnings=[note])) is None


def test_an_entry_id_is_the_file_name_as_a_slug() -> None:
    listing = Listing(
        "https://x.test/a.pdf", "https://x.test/", "Big Muff (v2) Build Doc.PDF"
    )

    assert entry_id(listing) == "big-muff-v2-build-doc"


def _boards(
    content: bytes, refs: list[str], listing: list[Location]
) -> dict[str, list[Location]]:
    """A board finder that finds each part once, below its parts list."""
    return {ref: [Location(1, 10, 20, 30, 40)] for ref in refs}


@pytest.fixture
def run(tmp_path: Path) -> Any:
    def go(
        web: _Web,
        read: Any,
        minutes: float = 60,
        documents: int | None = None,
        boards: Any = _boards,
    ) -> Reporter:
        reporter = Reporter(github=True, stream=io.StringIO())
        clock = _Clock()
        client = PoliteClient(web, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]
        budget = Budget(minutes * 60, clock=clock)
        CatalogRun(
            client,
            SOURCES[Publisher.PEDALPCB],
            tmp_path,
            reporter,
            budget,
            Sample(documents=documents),
            read,
            boards,
        ).run()
        return reporter

    return go


def _docs(web: _Web, body: bytes = b"%PDF fuzz") -> _Web:
    web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [
        _Response(body=body, headers={"ETag": '"1"'})
    ]
    web._pages["https://docs.pedalpcb.com/project/Drive.pdf"] = [
        _Response(body=b"%PDF drive")
    ]
    return web


def test_a_clean_document_is_listed_and_the_document_is_not_kept(
    tmp_path: Path, run: Any
) -> None:
    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading())

    entries = sorted((tmp_path / "builds" / "pedalpcb").glob("*.json"))
    assert [e.name for e in entries] == ["drive.json", "fuzz.json"]
    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["page"] == "https://www.pedalpcb.com/product/fuzz/"
    assert [p["ref"] for p in fuzz["builds"][0]["parts"]] == ["R1", "R2"]
    assert not list(tmp_path.rglob("*.pdf"))
    assert {e.outcome for e in reporter.tally.events} == {Outcome.LISTED}


def test_parts_counted_by_value_are_listed_with_their_count(
    tmp_path: Path, run: Any
) -> None:
    reading = _reading(parts=1)
    reading.inventory.add_counted(
        "doc", make_component_key(ComponentCategory.RESISTORS, "47"), 2
    )

    run(_docs(_pedalpcb_web()), lambda upload: reading)

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert [(p["ref"], p["count"], p["value"]) for p in fuzz["builds"][0]["parts"]] == [
        ("R1", 1, "10k"),
        (None, 2, "47"),
    ]


def test_an_unchanged_document_is_not_read_again(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    reads: list[FileUpload] = []

    def read(upload: FileUpload) -> ParseResult:
        reads.append(upload)
        return _reading()

    reporter = run(_docs(_pedalpcb_web()), read)

    assert reads == []
    assert {e.outcome for e in reporter.tally.events} == {Outcome.UNCHANGED}


def test_a_listed_build_says_where_its_board_draws_each_part(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["boards"]["R1"] == [
        {"page": 1, "x0": 10, "top": 20, "x1": 30, "bottom": 40}
    ]
    state = json.loads((tmp_path / "state" / "pedalpcb.json").read_text())
    assert {d["boards"] for d in state["documents"].values()} == {BOARDS_VERSION}


def test_an_entry_is_written_a_part_and_a_designator_per_line(
    tmp_path: Path, run: Any
) -> None:
    def noisy(*_: Any) -> dict[str, list[Location]]:
        return {"R1": [Location(1, 10.004999999, 20.5, 30.0, 101.51319999999998)]}

    run(_docs(_pedalpcb_web()), lambda upload: _reading(), boards=noisy)

    text = (tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text()
    lines = [line.strip().rstrip(",") for line in text.splitlines()]
    for part in json.loads(text)["builds"][0]["parts"]:
        assert json.dumps(part) in lines
    rounded = {"page": 1, "x0": 10.0, "top": 20.5, "x1": 30.0, "bottom": 101.51}
    assert f'"R1": {json.dumps([rounded])}' in lines
    state = (tmp_path / "state" / "pedalpcb.json").read_text()
    for document in json.loads(state)["documents"].values():
        assert json.dumps(document) in [
            line.split(": ", 1)[-1].rstrip(",") for line in state.splitlines()
        ]


def test_a_build_listed_without_board_positions_gains_them_unread(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    # As listed before board positions existed.
    state_path = tmp_path / "state" / "pedalpcb.json"
    state = json.loads(state_path.read_text())
    for document in state["documents"].values():
        del document["boards"]
    state_path.write_text(json.dumps(state))
    fuzz_path = tmp_path / "builds" / "pedalpcb" / "fuzz.json"
    fuzz = json.loads(fuzz_path.read_text())
    del fuzz["boards"]
    fuzz_path.write_text(json.dumps(fuzz))
    reads: list[FileUpload] = []
    web = _docs(_pedalpcb_web())

    def read(upload: FileUpload) -> ParseResult:
        reads.append(upload)
        return _reading()

    reporter = run(web, read)

    assert reads == []
    assert "boards" in json.loads(fuzz_path.read_text())
    # Fetched in full: a 304 would leave nothing to find them in.
    asked = dict(web.asked)
    assert "If-None-Match" not in asked["https://docs.pedalpcb.com/project/Fuzz.pdf"]
    fuzz_event = next(
        e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf")
    )
    assert fuzz_event.outcome == Outcome.UNCHANGED
    assert "board positions added" in fuzz_event.reason


def test_a_board_finder_failure_still_lists_the_build(tmp_path: Path, run: Any) -> None:
    def broken(*_: Any) -> dict[str, list[Location]]:
        raise ValueError("no pages")

    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading(), boards=broken)

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["boards"] == {}
    assert {e.outcome for e in reporter.tally.events} == {Outcome.LISTED}
    assert "couldn't find where fuzz draws its parts" in reporter._stream.getvalue()


def test_a_document_that_no_longer_reads_cleanly_is_taken_out(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())

    reporter = run(
        _docs(_pedalpcb_web(), body=b"%PDF fuzz v2"), lambda upload: _reading(parts=0)
    )

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    fuzz = next(e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf"))
    assert (fuzz.outcome, fuzz.reason) == (Outcome.NOT_CLEAN, "no parts were read")


def test_a_listed_document_found_not_to_be_a_build_is_removed(
    tmp_path: Path, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    fuzz = "https://docs.pedalpcb.com/project/Fuzz.pdf"
    source = replace(SOURCES[Publisher.PEDALPCB], not_builds={fuzz: "a test jig"})
    monkeypatch.setitem(SOURCES, Publisher.PEDALPCB, source)

    # The product page is unchanged, so its link comes from the last run.
    run(_docs(_pedalpcb_web()), lambda upload: _reading())

    entries = sorted((tmp_path / "builds" / "pedalpcb").glob("*.json"))
    assert [e.name for e in entries] == ["drive.json"]


def _without_fuzz(web: _Web) -> _Web:
    """The sitemap as it is some nights: without Fuzz's page."""
    web._pages["https://www.pedalpcb.com/product-sitemap.xml"] = [
        _Response(
            body=_PRODUCTS.replace(
                b"<url><loc>https://www.pedalpcb.com/product/fuzz/</loc><lastmod>2026-01-01</lastmod></url>",
                b"",
            )
        )
    ]
    return web


def test_a_page_the_sitemap_leaves_out_keeps_its_builds(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _without_fuzz(_docs(_pedalpcb_web()))

    reporter = run(web, lambda upload: _reading())

    # Visited, since the sitemap can't say it's gone.
    assert "https://www.pedalpcb.com/product/fuzz/" in web.urls()
    assert (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert Outcome.REMOVED not in {e.outcome for e in reporter.tally.events}
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    assert state.pages["https://www.pedalpcb.com/product/fuzz/"].lastmod == "2026-01-01"


def test_a_document_whose_page_is_gone_is_removed(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _without_fuzz(_docs(_pedalpcb_web()))
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [_Response(404)]

    reporter = run(web, lambda upload: _reading())

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert Outcome.REMOVED in {e.outcome for e in reporter.tally.events}
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    assert "https://www.pedalpcb.com/product/fuzz/" not in state.pages


def test_a_document_its_page_no_longer_links_is_removed(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    web._pages["https://www.pedalpcb.com/product-sitemap.xml"] = [
        _Response(body=_PRODUCTS.replace(b"2026-01-01", b"2026-03-03"))
    ]
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [
        _Response(body=b"<p>Sold out</p>")
    ]

    reporter = run(web, lambda upload: _reading())

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert Outcome.REMOVED in {e.outcome for e in reporter.tally.events}


def test_a_page_that_fails_keeps_the_builds_it_linked(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    # Fuzz's page has changed since, so it's fetched again, and it fails.
    web._pages["https://www.pedalpcb.com/product-sitemap.xml"] = [
        _Response(body=_PRODUCTS.replace(b"2026-01-01", b"2026-03-03"))
    ]
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [_Response(500)]

    reporter = run(web, lambda upload: _reading())

    entries = sorted((tmp_path / "builds" / "pedalpcb").glob("*.json"))
    assert [e.name for e in entries] == ["drive.json", "fuzz.json"]
    assert Outcome.REMOVED not in {e.outcome for e in reporter.tally.events}


def test_a_reading_that_fails_is_reported_and_the_rest_go_on(
    tmp_path: Path, run: Any
) -> None:
    def read(upload: FileUpload) -> ParseResult:
        if upload.name == "Fuzz.pdf":
            raise RuntimeError("boom")
        return _reading()

    reporter = run(_docs(_pedalpcb_web()), read)

    outcomes = {e.document.rsplit("/", 1)[-1]: e for e in reporter.tally.events}
    assert outcomes["Fuzz.pdf"].outcome == Outcome.FAILED
    assert "boom" in outcomes["Fuzz.pdf"].reason
    assert outcomes["Drive.pdf"].outcome == Outcome.LISTED
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    assert (
        state.documents["https://docs.pedalpcb.com/project/Fuzz.pdf"].outcome
        == Outcome.FAILED
    )


def test_a_document_that_fails_again_is_listed_but_known(
    tmp_path: Path, run: Any
) -> None:
    def broken() -> _Web:
        web = _docs(_pedalpcb_web())
        web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_Response(500)]
        return web

    def fuzz(reporter: Reporter) -> Event:
        return next(e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf"))

    first = fuzz(run(broken(), lambda upload: _reading()))
    second = fuzz(run(broken(), lambda upload: _reading()))

    assert (first.outcome, first.again) == (Outcome.FAILED, False)
    assert (second.outcome, second.again) == (Outcome.FAILED, True)
    assert second.reason.endswith("(it failed last run too)")


def test_a_run_out_of_time_reads_no_more_and_removes_nothing(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())

    reporter = run(web, lambda upload: _reading(), minutes=0)

    assert [u for u in web.urls() if "/product/" in u or u.endswith(".pdf")] == []
    assert (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert {e.outcome for e in reporter.tally.events} == {Outcome.POSTPONED}
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    assert len(state.pages) == 2
    assert all(page.postponed for page in state.pages.values())
    assert all(document.postponed for document in state.documents.values())


def test_pages_a_run_ran_out_of_time_for_go_first() -> None:
    web = _pedalpcb_web()
    drive = "https://www.pedalpcb.com/product/drive/"
    fuzz = "https://www.pedalpcb.com/product/fuzz/"
    known = {
        drive: PageState("old", (), postponed=True),
        fuzz: PageState("old", ()),
    }
    calls = iter([False, True])

    found = discover(
        _client(web), SOURCES[Publisher.PEDALPCB], known, spent=lambda: next(calls)
    )

    pages = [u for u in web.urls() if "/product/" in u]
    assert pages == [drive]
    assert not found.complete
    assert not found.pages[drive].postponed
    # The page left over keeps what it held last time, and goes first next.
    assert found.pages[fuzz] == PageState("old", (), postponed=True)


def test_documents_a_run_ran_out_of_time_for_go_first(
    tmp_path: Path, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Read after discovery, as for every publisher but PedalPCB.
    pedalpcb = replace(SOURCES[Publisher.PEDALPCB], documents_apart=False)
    monkeypatch.setitem(SOURCES, Publisher.PEDALPCB, pedalpcb)
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    state_path = tmp_path / "state" / "pedalpcb.json"
    state = State.load(state_path)
    fuzz = "https://docs.pedalpcb.com/project/Fuzz.pdf"
    state.documents[fuzz] = replace(state.documents[fuzz], postponed=True)
    state.save(state_path)
    web = _docs(_pedalpcb_web())

    run(web, lambda upload: _reading())

    documents = [u for u in web.urls() if u.endswith(".pdf")]
    assert documents == [fuzz, "https://docs.pedalpcb.com/project/Drive.pdf"]
    assert not any(d.postponed for d in State.load(state_path).documents.values())


def test_a_run_that_finds_nothing_new_leaves_the_state_as_it_was(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    state_path = tmp_path / "state" / "pedalpcb.json"
    before = state_path.read_text()

    run(_docs(_pedalpcb_web()), lambda upload: _reading())

    assert state_path.read_text() == before


def test_a_document_hash_ignores_what_follows_the_end_marker() -> None:
    body = b"%PDF-1.5 ... %%EOF\r\n"
    stamped = body + b"%tonepad.com 192.0.2.1 10/30/2026 9:21:00 PM"

    assert document_hash(stamped) == document_hash(body + b"%other stamp")
    assert document_hash(stamped) != document_hash(b"%PDF-1.5 changed %%EOF")


def test_the_summary_is_also_written_to_a_file(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    reporter = Reporter(
        github=False, console=Console(record=True), summary_file=summary
    )

    reporter.outcome(Event("tonepad", "https://x.test/a.pdf", Outcome.LISTED))
    reporter.summary()

    text = summary.read_text()
    assert text.startswith("## Circuit catalog")
    assert "| tonepad | 1 |" in text


# --- Reporting ---


def test_in_github_actions_failures_annotate_the_run(tmp_path: Path) -> None:
    out = io.StringIO()
    summary = tmp_path / "summary.md"
    reporter = Reporter(github=True, stream=out, summary_path=str(summary))

    with reporter.section("pedalpcb: reading"):
        reporter.outcome(Event("pedalpcb", "https://x.test/a.pdf", Outcome.LISTED))
        reporter.outcome(
            Event(
                "pedalpcb",
                "https://x.test/b.pdf",
                Outcome.FAILED,
                "50% done\nthen broke",
            )
        )
    reporter.summary()

    text = out.getvalue()
    assert text.startswith("::group::pedalpcb: reading\n")
    assert "::endgroup::" in text
    assert (
        "::warning title=pedalpcb%3A failed::https://x.test/b.pdf (50%25 done%0Athen broke)"
        in text
    )
    assert "| pedalpcb | 1 |" in summary.read_text()
    assert "https://x.test/b.pdf" in summary.read_text()


def test_outside_github_actions_the_output_is_rich() -> None:
    console = Console(record=True, width=120)
    reporter = Reporter(github=False, console=console)

    reporter.outcome(
        Event(
            "tonepad", "https://x.test/a.pdf", Outcome.NOT_CLEAN, "no parts were read"
        )
    )
    reporter.summary()

    text = console.export_text()
    assert "not clean" in text
    assert "no parts were read" in text
    assert "Not added" in text
    assert "::" not in text


def test_the_environment_picks_the_form() -> None:
    assert Reporter.for_environment({"GITHUB_ACTIONS": "true"}).github
    assert not Reporter.for_environment({}).github


def test_aion_fx_is_not_in_the_catalog() -> None:
    assert {s.publisher for s in SOURCES.values()} == set(Publisher)
    assert all("aionfx" not in s.site for s in SOURCES.values())


class _Conditional(_Web):
    """A fake web whose documents answer a conditional request with a 304."""

    def get(self, url: str, headers: dict[str, str], **kwargs: Any) -> _Response:
        if url.endswith(".pdf") and "If-None-Match" in headers:
            self.asked.append((url, headers))
            return _Response(304)
        return super().get(url, headers, **kwargs)


def _conditional(body: bytes = b"%PDF fuzz") -> _Web:
    web = _Conditional({})
    web._pages = _docs(_pedalpcb_web(), body)._pages
    return web


def _fuzz_event(reporter: Reporter) -> Event:
    return next(e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf"))


def test_a_build_whose_document_returns_unchanged_is_listed_again(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    gone = _docs(_pedalpcb_web())
    gone._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_Response(404)]
    run(gone, lambda upload: _reading())
    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    web = _conditional()

    reporter = run(web, lambda upload: _reading())

    # Fetched in full: its ETag is from before it went, so a 304 says nothing.
    fuzz_asks = [h for u, h in web.asked if u.endswith("Fuzz.pdf")]
    assert fuzz_asks == [{}]
    assert (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert _fuzz_event(reporter).outcome == Outcome.LISTED


def test_a_resolver_failure_keeps_the_builds_listed(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_unresolved()]

    reporter = run(web, lambda upload: _reading())

    assert (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert _fuzz_event(reporter).outcome == Outcome.FAILED


def test_a_changed_document_that_fails_to_read_keeps_its_last_reading(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    fuzz_path = tmp_path / "builds" / "pedalpcb" / "fuzz.json"
    listed = fuzz_path.read_text()

    def broken(upload: FileUpload) -> ParseResult:
        raise RuntimeError("boom")

    first = _fuzz_event(run(_docs(_pedalpcb_web(), b"%PDF fuzz v2"), broken))
    # Builders still see the last good reading.
    assert fuzz_path.read_text() == listed
    second = _fuzz_event(run(_conditional(b"%PDF fuzz v2"), broken))
    # Tried again in full; failing twice is known, not news.
    assert (first.outcome, first.again) == (Outcome.FAILED, False)
    assert (second.outcome, second.again) == (Outcome.FAILED, True)
    assert fuzz_path.read_text() == listed

    reporter = run(_conditional(b"%PDF fuzz v2"), lambda upload: _reading(parts=3))

    assert _fuzz_event(reporter).outcome == Outcome.UPDATED
    fuzz = json.loads(fuzz_path.read_text())
    assert fuzz["sha256"] == document_hash(b"%PDF fuzz v2")
    assert len(fuzz["builds"][0]["parts"]) == 3


def test_a_document_back_from_a_failure_is_not_reported_as_failing_again(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())

    def broken() -> _Web:
        web = _docs(_pedalpcb_web())
        web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_Response(500)]
        return web

    run(broken(), lambda upload: _reading())
    back = _fuzz_event(run(_conditional(), lambda upload: _reading()))
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    failed = _fuzz_event(run(broken(), lambda upload: _reading()))

    assert back.outcome == Outcome.UNCHANGED
    fuzz = state.documents["https://docs.pedalpcb.com/project/Fuzz.pdf"]
    assert fuzz.outcome == Outcome.UNCHANGED
    assert (failed.outcome, failed.again) == (Outcome.FAILED, False)


def test_a_dead_link_hides_its_build(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_Response(404)]

    reporter = run(web, lambda upload: _reading())

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    fuzz = next(e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf"))
    assert fuzz.outcome == Outcome.GONE


# --- Verified by hand ---


def test_a_fingerprint_is_what_is_bought_not_where_or_in_what_order() -> None:
    a = _reading(parts=2)
    b = Inventory()
    for n in (2, 1):
        b.add_part(
            "other", make_component_key(ComponentCategory.RESISTORS, "10k"), f"R{n}"
        )
    c = _reading(parts=2)
    c.inventory.add_part(
        "doc", make_component_key(ComponentCategory.RESISTORS, "1k"), "R3"
    )

    assert fingerprint(a) == fingerprint(ParseResult(b, ReadingStats(), title="Other"))
    assert fingerprint(a) != fingerprint(c)


def test_a_fingerprint_counts_parts_counted_by_value() -> None:
    def counting(count: int) -> ParseResult:
        reading = _reading()
        reading.inventory.add_counted(
            "doc", make_component_key(ComponentCategory.RESISTORS, "47"), count
        )
        return reading

    assert fingerprint(counting(2)) != fingerprint(counting(3))
    assert fingerprint(counting(2)) != fingerprint(_reading())
    # A reading that counts nothing keeps the fingerprint it was checked with.
    assert (
        fingerprint(_reading())
        == "4a55a41713604242c7756e7dcdba2e66fe4eb1ca07c891e85e99dbd6fd5e4c23"
    )


def test_a_document_is_verified_only_with_its_checked_reading(tmp_path: Path) -> None:
    listed = VerifiedList({"abc": Checked(fingerprint(_reading()), "2026-10-30")})
    listed.save(tmp_path / "verified.json")
    loaded = VerifiedList.load(tmp_path / "verified.json")

    assert loaded.check("abc", fingerprint(_reading())) == Verification.VERIFIED
    assert loaded.check("abc", fingerprint(_reading(parts=3))) == Verification.CHANGED
    assert loaded.check("def", fingerprint(_reading())) == Verification.UNVERIFIED


def _verify(tmp_path: Path, body: bytes, reading: ParseResult) -> None:
    digest = hashlib.sha256(body).hexdigest()
    VerifiedList({digest: Checked(fingerprint(reading), "2026-10-30")}).save(
        tmp_path / "verified.json"
    )


def test_a_verified_document_is_listed_and_marked(tmp_path: Path, run: Any) -> None:
    doubt = ParseWarning(ParseWarningKind.UNEXTRACTED_REF, "C4", "C4 may be missing.")
    checked = _reading(warnings=[doubt])
    _verify(tmp_path, b"%PDF fuzz", checked)

    def read(upload: FileUpload) -> ParseResult:
        return checked if upload.name == "Fuzz.pdf" else _reading()

    reporter = run(_docs(_pedalpcb_web()), read)

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    drive = json.loads((tmp_path / "builds" / "pedalpcb" / "drive.json").read_text())
    # Listed despite its warning, because it was checked by hand.
    assert fuzz["verified"] is True
    # An unchecked document with no doubts is listed, unmarked.
    assert drive["verified"] is False
    assert reporter._verified("pedalpcb") == 1


def test_a_verified_document_with_no_parts_is_not_listed(
    tmp_path: Path, run: Any
) -> None:
    empty = _reading(parts=0)
    _verify(tmp_path, b"%PDF fuzz", empty)

    def read(upload: FileUpload) -> ParseResult:
        return empty if upload.name == "Fuzz.pdf" else _reading()

    run(_docs(_pedalpcb_web()), read)

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()


def test_a_checked_file_that_now_reads_differently_loses_its_mark(
    tmp_path: Path, run: Any
) -> None:
    _verify(tmp_path, b"%PDF fuzz", _reading(parts=3))

    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading())

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["verified"] is False
    assert "likely a parser regression" in reporter._stream.getvalue()


def test_a_checked_document_is_read_again_by_a_new_version(
    tmp_path: Path, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verify(tmp_path, b"%PDF fuzz", _reading())
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    reads: list[str] = []

    def regressed(upload: FileUpload) -> ParseResult:
        reads.append(upload.name)
        return _reading(parts=3)

    # The same version doesn't read an unchanged document again.
    run(_conditional(), regressed)
    assert reads == []
    monkeypatch.setattr("star_ground.catalog.build.version", lambda: "99.0.0")

    reporter = run(_conditional(), regressed)

    assert reads == ["Fuzz.pdf"]
    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["verified"] is False
    assert "likely a parser regression" in reporter._stream.getvalue()
    assert _fuzz_event(reporter).outcome == Outcome.UPDATED


def test_a_checked_document_that_reads_the_same_keeps_its_mark(
    tmp_path: Path, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _verify(tmp_path, b"%PDF fuzz", _reading())
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    monkeypatch.setattr("star_ground.catalog.build.version", lambda: "99.0.0")

    reporter = run(_conditional(), lambda upload: _reading())

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["verified"] is True
    event = _fuzz_event(reporter)
    assert (event.outcome, event.reason) == (Outcome.UNCHANGED, "read again")
    state = State.load(tmp_path / "state" / "pedalpcb.json")
    assert state.documents[fuzz["document"]].reader == "99.0.0"


def test_a_newly_verified_unchanged_document_gains_its_mark(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    _verify(tmp_path, b"%PDF fuzz", _reading())

    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading())

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["verified"] is True
    assert {e.outcome for e in reporter.tally.events} == {Outcome.UNCHANGED}


def test_a_rejected_document_checked_since_is_fetched_and_listed(
    tmp_path: Path, run: Any
) -> None:
    doubt = ParseWarning(ParseWarningKind.UNPARSED_VALUE, "R1", "Check its value.")
    doubtful = _reading(warnings=[doubt])
    run(_docs(_pedalpcb_web()), lambda upload: doubtful)
    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    _verify(tmp_path, b"%PDF fuzz", doubtful)
    web = _docs(_pedalpcb_web())

    run(web, lambda upload: doubtful)

    assert json.loads((tmp_path / "builds/pedalpcb/fuzz.json").read_text())["verified"]
    fuzz_asks = [h for u, h in web.asked if u.endswith("Fuzz.pdf")]
    assert fuzz_asks == [{}]


def test_a_failure_while_discovering_alongside_reading_is_raised(
    run: Any, mocker: Any
) -> None:
    mocker.patch(
        "star_ground.catalog.build.discover", side_effect=RuntimeError("sitemap broke")
    )

    with pytest.raises(RuntimeError, match="sitemap broke"):
        run(_docs(_pedalpcb_web()), lambda upload: _reading())


def test_a_checked_document_that_still_is_not_clean_settles(
    tmp_path: Path, run: Any
) -> None:
    doubt = ParseWarning(ParseWarningKind.UNPARSED_VALUE, "R1", "Check its value.")
    doubtful = _reading(warnings=[doubt])
    run(_docs(_pedalpcb_web()), lambda upload: doubtful)
    # Checked by hand, but with a reading other than today's.
    _verify(tmp_path, b"%PDF fuzz", _reading(parts=3, warnings=[doubt]))
    reads: list[str] = []

    def read(upload: FileUpload) -> ParseResult:
        reads.append(upload.name)
        return doubtful

    run(_conditional(), read)
    assert reads == ["Fuzz.pdf"]
    web = _conditional()

    reporter = run(web, read)

    # Read once against the list; after that it's asked for conditionally.
    assert reads == ["Fuzz.pdf"]
    assert [h for u, h in web.asked if u.endswith("Fuzz.pdf")] == [
        {"If-None-Match": '"1"'}
    ]
    assert _fuzz_event(reporter).outcome == Outcome.NOT_CLEAN


def test_a_document_redirected_off_the_site_is_not_fetched(
    tmp_path: Path, run: Any
) -> None:
    web = _docs(_pedalpcb_web())
    web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [
        _moved("https://elsewhere.example/Fuzz.pdf")
    ]

    reporter = run(web, lambda upload: _reading())

    assert not any("elsewhere" in u for u, _ in web.asked)
    assert _fuzz_event(reporter).outcome == Outcome.DISALLOWED


def test_discovery_stops_and_is_waited_for_when_reading_fails(
    run: Any, mocker: Any
) -> None:
    finished = threading.Event()

    def endless(*args: Any, **kwargs: Any) -> Discovery:
        spent = args[4]
        kwargs["on_listing"](
            Listing("https://docs.pedalpcb.com/project/Fuzz.pdf", "p", "Fuzz.pdf")
        )
        # Crawls until told to stop (or, failing that, gives up after a while).
        for _ in range(500):
            if spent():
                break
            time.sleep(0.01)
        finished.set()
        return Discovery()

    mocker.patch("star_ground.catalog.build.discover", side_effect=endless)
    mocker.patch.object(CatalogRun, "read_one", side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        run(_docs(_pedalpcb_web()), lambda upload: _reading())

    assert finished.is_set()


def test_a_run_saves_its_state_once_at_the_end(run: Any, mocker: Any) -> None:
    web = _pedalpcb_web()
    for page in ("fuzz", "drive"):
        web._pages[f"https://www.pedalpcb.com/product/{page}/"] = [_Response()]
    save = mocker.spy(State, "save")

    run(web, lambda upload: _reading())

    assert save.call_count == 1


def test_state_is_written_whole_or_not_at_all(
    tmp_path: Path, run: Any, mocker: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    path = tmp_path / "state" / "pedalpcb.json"
    before = path.read_text()
    mocker.patch(
        "star_ground.catalog.entries.os.replace", side_effect=KeyboardInterrupt
    )

    with pytest.raises(KeyboardInterrupt):
        State().save(path)

    # The last state is still there, whole, and nothing is left beside it.
    assert path.read_text() == before
    State.load(path)
    assert [p.name for p in path.parent.iterdir()] == ["pedalpcb.json"]


def test_an_entry_is_written_whole_or_not_at_all(
    tmp_path: Path, run: Any, mocker: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    path = tmp_path / "builds" / "pedalpcb" / "fuzz.json"
    before = path.read_text()
    entry = CatalogEntry.load(path)
    mocker.patch("star_ground.catalog.entries.os.fdopen", side_effect=OSError("full"))

    with pytest.raises(OSError, match="full"):
        replace(entry, title="Other").write(path)

    assert path.read_text() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["drive.json", "fuzz.json"]


def test_pedalpcb_documents_are_read_while_discovery_goes_on() -> None:
    assert SOURCES[Publisher.PEDALPCB].documents_apart
    assert not any(
        s.documents_apart for p, s in SOURCES.items() if p != Publisher.PEDALPCB
    )


# --- What each build is: its name and kind of effect ---


_WOOCOMMERCE = """<body class="product-template postid-5">
<nav class="woocommerce-breadcrumb"><a href="https://x.test/">Home</a> /
<a href="https://x.test/product-category/pcbs/fuzz/">Fuzz</a> / Acid Rain Fuzz</nav>
<div id="product-5" class="product type-product post-5 product_cat-od-distortion-fuzz product_cat-fuzz">
<h1 class="product_title">Acid Rain Fuzz</h1></div>
<li class="product type-product post-9 product_cat-delay">A related delay</li>
</body>"""


def test_a_product_page_names_its_product_and_its_categories() -> None:
    assert page_details(_WOOCOMMERCE) == PageDetails(
        "Acid Rain Fuzz", ("fuzz", "od-distortion-fuzz")
    )


def test_a_page_without_product_classes_gives_its_breadcrumb_s_category() -> None:
    page = """<body class="postid-7"><h1>Albus &#8211; Mad Professor Simble
    Overdrive Clone</h1><nav class="woocommerce-breadcrumb">
    <a href="https://x.test/product-category/diy-guitar-effects-pedal-pcbs/amp-in-a-box/">Amp</a>
    </nav></body>"""

    assert page_details(page) == PageDetails(
        "Albus \u2013 Mad Professor Simble Overdrive Clone", ("amp-in-a-box",)
    )


def test_tonepad_names_a_project_and_its_kind_of_effect() -> None:
    page = """<p><span class="projectLabel">Pulsar</span> -- INTERMEDIATE</p>
    <p>More tremolo projects:</p><p>More INTERMEDIATE level projects:</p>"""

    assert page_details(page) == PageDetails("Pulsar", ("tremolo",))


def test_a_dedicated_list_names_each_document_by_its_link() -> None:
    source = SOURCES[Publisher.GUITARPCB]
    listed = pdf_links(
        "https://guitarpcb.com/legacy-pcb-document-list/",
        '<h1>Legacy</h1><a href="https://guitarpcb.com/a/BD_PURP.pdf">'
        "*PURP \u2013 Pump\u2019d Up Rangemaster Plus</a>",
        source,
    )
    labelled = pdf_links(
        "https://guitarpcb.com/community/topic/pedal-building-guides-mandatory-reading/",
        '<h1>New GuitarPCB Guide:</h1><a href="https://guitarpcb.com/a/Kicker.pdf">'
        "Build Document (PDF)</a>",
        source,
    )

    assert [li.title for li in listed] == [
        "PURP \u2013 Pump\u2019d Up Rangemaster Plus"
    ]
    assert [li.title for li in labelled] == [None]


def test_publishers_categories_name_one_list_of_effects() -> None:
    # A parent category over three kinds names none of them.
    assert from_categories(("pcbs", "od-distortion-fuzz", "fuzz")) == [Effect.FUZZ]
    assert from_categories(("od-distortion-fuzz",)) == []
    assert from_categories(("amp-in-a-box",)) == [Effect.OVERDRIVE]
    assert from_categories(("tremolo",)) == [Effect.MODULATION]
    assert from_categories(("boosts", "dynamics")) == [Effect.BOOST, Effect.DYNAMICS]


def test_a_title_s_words_name_its_effects() -> None:
    assert from_title("Acid Rain Fuzz") == [Effect.FUZZ]
    assert from_title("Bass Overdriver") == []
    assert from_title("Rattlesnake") == []
    assert from_title("Dr. Phybes Phaser") == [Effect.MODULATION]
    # 'Power' and 'amp' place a build only as a publisher's category.
    assert from_title("NostalgiTone Tower Power") == []
    assert from_title("BMP Op Amp") == []


def test_effects_set_by_hand_come_first_then_the_title_s_then_the_page_s() -> None:
    overrides = Overrides({"pedalpcb/fuzz": [Effect.BOOST]})

    assert effects("pedalpcb/fuzz", "Fuzz", ("delay",), overrides) == [Effect.BOOST]
    assert effects("pedalpcb/x", "Fuzz", ("delay",), overrides) == [Effect.FUZZ]
    assert effects("pedalpcb/x", "Thing", ("delay",), overrides) == [Effect.DELAY]
    assert effects("pedalpcb/x", "Thing", (), overrides) == []


def test_a_build_s_title_is_the_document_s_then_the_page_s_then_the_file_s() -> None:
    plain = Listing(
        "https://x.test/BD_FuzzyBee.pdf", "https://x.test/", "MUFFN-v4-2022.pdf"
    )
    named = replace(plain, title="Albus \u2013 Mad Professor Simble Overdrive Clone")
    five_cats = replace(plain, name="FCP-Albus-V1-0-Insert.pdf")

    assert entry_title("Muff'n", named) == "Muff'n"
    assert entry_title(None, named) == "Albus"
    assert entry_title(None, plain) == "MUFFN v4 2022"
    assert entry_title(None, five_cats) == "Albus v1.0"
    champ = replace(plain, title="Micro Champ- Lovepedal Champ Clone")
    assert entry_title(None, champ) == "Micro Champ"
    assert entry_title(None, replace(plain, title="BE-My-OD")) == "BE-My-OD"


def test_a_page_read_by_an_older_version_is_read_again() -> None:
    web = _pedalpcb_web()
    fuzz = Listing(
        "https://docs.pedalpcb.com/project/Fuzz.pdf",
        "https://www.pedalpcb.com/product/fuzz/",
        "Fuzz.pdf",
    )
    page = "https://www.pedalpcb.com/product/fuzz/"
    known = {page: PageState("2026-01-01", (fuzz,), version=PAGE_VERSION - 1)}

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], known)

    assert page in web.urls()
    assert found.pages[page].version == PAGE_VERSION


def test_a_build_takes_its_name_and_effects_from_its_page(
    tmp_path: Path, run: Any
) -> None:
    web = _docs(_pedalpcb_web())
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [
        _Response(
            body=b'<body class="postid-3"><div class="post-3 product_cat-delay">'
            b"<h1>Chamber</h1></div>"
            b'<a href="https://docs.pedalpcb.com/project/Fuzz.pdf">Build Docs</a></body>'
        )
    ]

    run(web, lambda upload: replace(_reading(), title=None))

    fuzz = json.loads((tmp_path / "builds" / "pedalpcb" / "fuzz.json").read_text())
    assert fuzz["title"] == "Chamber"
    assert fuzz["effects"] == ["delay"]


def test_effects_set_by_hand_reach_a_build_that_is_not_read_again(
    tmp_path: Path, run: Any
) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    fuzz_path = tmp_path / "builds" / "pedalpcb" / "fuzz.json"
    assert json.loads(fuzz_path.read_text())["effects"] == ["fuzz"]
    (tmp_path / "effects.json").write_text('{"pedalpcb/fuzz": ["boost"]}')

    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading())

    assert json.loads(fuzz_path.read_text())["effects"] == ["boost"]
    assert {e.outcome for e in reporter.tally.events} == {Outcome.UNCHANGED}


def test_a_document_listed_and_sold_takes_its_product_page_s_categories() -> None:
    document = "https://guitarpcb.com/wp-content/uploads/BD_Swah.pdf"
    product = "https://guitarpcb.com/product/swah/"
    listed = "https://guitarpcb.com/legacy-pcb-document-list/"
    web = _Web(
        {
            "https://guitarpcb.com/wp-sitemap.xml": _Response(
                body=b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://guitarpcb.com/wp-sitemap-posts-product-1.xml</loc></sitemap>
</sitemapindex>"""
            ),
            "https://guitarpcb.com/wp-sitemap-posts-product-1.xml": _Response(
                body=f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{product}</loc></url></urlset>""".encode()
            ),
            listed: _Response(body=f'<a href="{document}">SWAH v4</a>'.encode()),
            product: _Response(
                body=f"""<body class="postid-2"><div class="post-2 product_cat-modulation">
                <a href="{document}">Build Document</a></div></body>""".encode()
            ),
        }
    )

    found = discover(_client(web), SOURCES[Publisher.GUITARPCB])

    assert found.listings[document].title == "SWAH v4"
    assert found.listings[document].categories == ("modulation",)


def test_a_bundle_s_page_does_not_name_each_document_in_it() -> None:
    page = "https://www.five-cats-pedals.co.uk/product/eqd-mega-pack/"
    links = pdf_links(
        page,
        """<h1>EQD Mega Pack</h1>
        <a href="https://www.five-cats-pedals.co.uk/u/FCP-Shouter.pdf">Shouter build doc</a>
        <a href="https://www.five-cats-pedals.co.uk/u/FCP-Spanker.pdf">Spanker build doc</a>""",
        SOURCES[Publisher.FIVECATS],
    )

    assert [li.title for li in links] == [None, None]


def test_every_committed_entry_reads_and_writes_back_unchanged(tmp_path: Path) -> None:
    """The website reads entries as they are; the typed model must keep them so."""
    entries = sorted(
        (Path(__file__).parent.parent / "catalog" / "builds").glob("*/*.json")
    )
    assert entries
    for path in entries:
        copy = tmp_path / path.name
        CatalogEntry.load(path).write(copy)
        assert copy.read_text() == path.read_text(), path.name
