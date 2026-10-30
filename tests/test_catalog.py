"""Tests for the catalog job (`src.catalog`), with a fake web and fake readers."""

import io
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import requests
from rich.console import Console

from src.bom_lib import (
    ComponentCategory,
    FileUpload,
    ParseResult,
    ParseWarning,
    ParseWarningKind,
    create_empty_inventory,
    create_empty_stats,
)
from src.bom_lib.types import make_component_key
from src.catalog.build import Budget, State, build, clean, entry_id
from src.catalog.client import (
    MIN_DELAY_S,
    BlockedError,
    DisallowedError,
    GoneError,
    PoliteClient,
)
from src.catalog.discover import Listing, PageState, discover, pdf_links
from src.catalog.report import Event, Outcome, Reporter
from src.catalog.sources import SOURCES, Publisher


@dataclass
class _Response:
    status_code: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    cut_short: bool = False

    @property
    def text(self) -> str:
        return self.body.decode()

    def iter_content(self, size: int) -> Iterator[bytes]:
        yield self.body
        if self.cut_short:
            raise requests.exceptions.ChunkedEncodingError("length mismatch")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def close(self) -> None:
        pass

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        pass


class _Web:
    """A fake session: answers by URL, in turn when a URL has several answers."""

    def __init__(self, pages: Mapping[str, _Response | list[_Response]]) -> None:
        self.headers: dict[str, str] = {}
        self._pages = {k: v if isinstance(v, list) else [v] for k, v in pages.items()}
        self.asked: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, headers: dict[str, str], **_: Any) -> _Response:
        self.asked.append((url, headers))
        if url.endswith("/robots.txt") and url not in self._pages:
            return _Response(404)
        answers = self._pages.get(url) or [_Response(404)]
        return answers.pop(0) if len(answers) > 1 else answers[0]

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


def _client(web: _Web, clock: _Clock | None = None) -> PoliteClient:
    clock = clock or _Clock()
    return PoliteClient(web, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]


# --- The polite client ---


def test_every_request_says_it_is_star_ground() -> None:
    web = _Web({"https://a.test/x": _Response(body=b"hi")})

    _client(web).get("https://a.test/x")

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
        _client(web).get("https://a.test/private/doc.pdf")

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

    client.get("https://a.test/1")
    client.get("https://a.test/2")

    # robots.txt, then each page: never closer than the larger delay.
    assert len(clock.slept) == 2
    assert all(s >= 30 for s in clock.slept)


def test_requests_wait_at_least_the_minimum() -> None:
    web = _Web({"https://a.test/1": _Response()})
    clock = _Clock()

    _client(web, clock).get("https://a.test/1")

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
        client.get("https://a.test/1")
    with pytest.raises(BlockedError):
        client.get("https://a.test/2")

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

    fetched = _client(web, clock).get("https://a.test/1")

    assert fetched.content == b"ok"
    assert 120 in clock.slept


def test_too_many_requests_that_persists_stops_the_site() -> None:
    web = _Web({"https://a.test/1": _Response(429)})

    with pytest.raises(BlockedError, match="429"):
        _client(web).get("https://a.test/1")


def test_an_unchanged_document_costs_a_304() -> None:
    web = _Web({"https://a.test/d.pdf": _Response(304)})

    fetched = _client(web).get("https://a.test/d.pdf", etag='"v1"', last_modified="Mon")

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

    assert _client(web).get("https://a.test/d.pdf").content.endswith(b"%%EOF\n")


def test_a_pdf_cut_short_before_its_end_is_a_failure() -> None:
    web = _Web(
        {"https://a.test/d.pdf": _Response(body=b"%PDF-1.4 ...", cut_short=True)}
    )

    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        _client(web).get("https://a.test/d.pdf")


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


def test_a_page_that_fails_is_reported_and_the_rest_go_on() -> None:
    web = _pedalpcb_web()
    web._pages["https://www.pedalpcb.com/product/fuzz/"] = [_Response(500)] * 5

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB])

    assert "https://www.pedalpcb.com/product/fuzz/" in found.failures
    assert list(found.listings) == ["https://docs.pedalpcb.com/project/Drive.pdf"]


def test_a_dead_host_is_gone_not_failed() -> None:
    class _Dead(_Web):
        def get(self, url: str, headers: dict[str, str], **_: Any) -> _Response:
            raise requests.exceptions.ConnectionError(
                "NameResolutionError: no such host"
            )

    with pytest.raises(GoneError, match="no longer exists"):
        _client(_Dead({})).get("http://old.test/doc.pdf")


def test_a_missing_document_is_gone() -> None:
    web = _Web({"https://a.test/d.pdf": _Response(404)})

    with pytest.raises(GoneError, match="404"):
        _client(web).get("https://a.test/d.pdf")


def test_a_sample_spreads_across_the_pages() -> None:
    web = _pedalpcb_web()

    found = discover(_client(web), SOURCES[Publisher.PEDALPCB], limit=1)

    assert len(found.pages) == 1


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
    inventory = create_empty_inventory()
    for n in range(1, parts + 1):
        inventory.add_part(
            "doc", make_component_key(ComponentCategory.RESISTORS, "10k"), f"R{n}"
        )
    stats = create_empty_stats()
    stats["warnings"] = warnings or []
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


@pytest.fixture
def run(tmp_path: Path) -> Any:
    def go(
        web: _Web, read: Any, minutes: float = 60, documents: int | None = None
    ) -> Reporter:
        reporter = Reporter(github=True, stream=io.StringIO())
        clock = _Clock()
        client = PoliteClient(web, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type]
        budget = Budget(minutes * 60, clock=clock)
        build(
            client,
            SOURCES[Publisher.PEDALPCB],
            tmp_path,
            reporter,
            budget,
            None,
            documents,
            read,
        )
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


def test_an_unchanged_document_is_not_read_again(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    reads: list[FileUpload] = []

    def read(upload: FileUpload) -> ParseResult:
        reads.append(upload)
        return _reading()

    reporter = run(_docs(_pedalpcb_web()), read)

    assert reads == []
    assert {e.outcome for e in reporter.tally.events} == {Outcome.UNCHANGED}


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


def test_a_document_no_longer_linked_is_removed(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    web._pages["https://www.pedalpcb.com/product-sitemap.xml"] = [
        _Response(
            body=_PRODUCTS.replace(
                b"<url><loc>https://www.pedalpcb.com/product/fuzz/</loc><lastmod>2026-01-01</lastmod></url>",
                b"",
            )
        )
    ]

    reporter = run(web, lambda upload: _reading())

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    assert Outcome.REMOVED in {e.outcome for e in reporter.tally.events}


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


def test_documents_past_the_time_budget_are_postponed(run: Any) -> None:
    reporter = run(_docs(_pedalpcb_web()), lambda upload: _reading(), minutes=0)

    assert {e.outcome for e in reporter.tally.events} == {Outcome.POSTPONED}


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
        "::warning title=pedalpcb: failed::https://x.test/b.pdf (50%25 done%0Athen broke)"
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


def test_a_dead_link_hides_its_build(tmp_path: Path, run: Any) -> None:
    run(_docs(_pedalpcb_web()), lambda upload: _reading())
    web = _docs(_pedalpcb_web())
    web._pages["https://docs.pedalpcb.com/project/Fuzz.pdf"] = [_Response(404)]

    reporter = run(web, lambda upload: _reading())

    assert not (tmp_path / "builds" / "pedalpcb" / "fuzz.json").exists()
    fuzz = next(e for e in reporter.tally.events if e.document.endswith("Fuzz.pdf"))
    assert fuzz.outcome == Outcome.GONE
