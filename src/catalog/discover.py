"""Finds a publisher's build documents from the pages that list them.

Discovery reads only HTML and XML: sitemaps, product pages and document
lists. It never requests a document. A product page the sitemap says hasn't
changed since the last run (`<lastmod>`) isn't fetched again; its links are
taken from the last run (`PageState`).
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from src.catalog.client import PoliteClient
from src.catalog.sources import FileIndex, Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Listing:
    """A build document, as a publisher's page links it.

    Attributes:
        document: The document's URL.
        page: The page that links it: the product page to buy the board
            from, or a list of documents.
        name: The document's file name, as published.
    """

    document: str
    page: str
    name: str


@dataclass(frozen=True, slots=True)
class PageState:
    """A listing page as the last run read it.

    Attributes:
        lastmod: The sitemap's `<lastmod>` for it then, if it gave one.
        listings: The documents it linked.
        read: When it was last fetched (ISO 8601), so the pages read longest
            ago go first when a run's time is short.
    """

    lastmod: str | None
    listings: tuple[Listing, ...]
    read: str = ""


@dataclass(slots=True)
class Discovery:
    """What a run found.

    Attributes:
        listings: Every document found, each once, by URL.
        pages: Each listing page's state, for the next run.
        failures: Pages that couldn't be read, and why.
        complete: Whether every listing page was considered; a run out of
            time leaves the rest for the next, so it can't tell what's gone.
    """

    listings: dict[str, Listing] = field(default_factory=dict)
    pages: dict[str, PageState] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    complete: bool = True


@dataclass(frozen=True, slots=True)
class _Anchor:
    href: str
    label: str
    row: str


class _Anchors(HTMLParser):
    """A page's links: their text (with images' alt text) and table row heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._row: list[str] = []
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            self._href = attributes.get("href")
            self._label = []
        elif tag == "img" and self._href:
            self._label.append(attributes.get("alt") or "")
        elif tag == "tr":
            self._row = []
            self._in_heading = False
        elif tag == "th":
            self._in_heading = True

    def handle_data(self, data: str) -> None:
        if self._href:
            self._label.append(data)
        if self._in_heading:
            self._row.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "th":
            self._in_heading = False
        elif tag == "tr":
            self._row = []
        elif tag == "a" and self._href:
            self.anchors.append(
                _Anchor(
                    self._href,
                    " ".join(" ".join(self._label).split()),
                    " ".join(" ".join(self._row).split()),
                )
            )
            self._href = None


def anchors(html: str) -> list[_Anchor]:
    """Every link on a page."""
    parser = _Anchors()
    parser.feed(html)
    return parser.anchors


def _name(url: str) -> str:
    return PurePosixPath(unquote(urlsplit(url).path)).name


def pdf_links(page: str, html: str, source: Source) -> list[Listing]:
    """The build documents a product page or document list links.

    Args:
        page: The page's URL.
        html: The page.
        source: The publisher.

    Returns:
        Its build documents' links, in the order they appear.
    """
    listed = next((p for p in source.document_pages if p.url == page), None)
    on_product = source.product_path in urlsplit(page).path
    label_rule = (
        listed.label if listed else (source.product_label if on_product else None)
    )
    found: dict[str, Listing] = {}
    for anchor in anchors(html):
        url = urlsplit(urljoin(page, anchor.href.strip().strip("\"'")))
        document = urlunsplit(url._replace(fragment=""))
        if not source.allows(document) or not unquote(url.path).lower().endswith(
            ".pdf"
        ):
            continue
        by_row = (
            on_product
            and source.product_row_label is not None
            and source.product_row_label.search(anchor.row) is not None
        )
        if label_rule and not label_rule.search(anchor.label) and not by_row:
            continue
        clues = f"{unquote(url.path)} {anchor.label} {anchor.row}"
        if source.excluded and source.excluded.search(clues):
            continue
        found.setdefault(document, Listing(document, page, _name(document)))
    return list(found.values())


def _published_id(base: str, href: str, path: str, source: Source) -> str | None:
    """A published numeric-id link, normalized; ids are never guessed."""
    url = urlsplit(urljoin(base, href))
    query = parse_qs(url.query, keep_blank_values=True)
    ids = query.get("id", [])
    if (
        not source.allows(url.geturl())
        or url.path.casefold() != path.casefold()
        or set(query) != {"id"}
        or len(ids) != 1
        or not ids[0].isdigit()
    ):
        return None
    return urljoin(source.site, path.lstrip("/")) + f"?id={int(ids[0])}"


def _sitemap_pages(client: PoliteClient, source: Source) -> dict[str, str | None]:
    """Product pages from the sitemaps, with each one's `<lastmod>`."""
    assert source.sitemap is not None
    pending, seen = [source.sitemap], set[str]()
    pages: dict[str, str | None] = {}
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        logger.info("Sitemap %s", url)
        root = ElementTree.fromstring(client.get(url).content)
        kind = root.tag.rsplit("}", 1)[-1]
        if kind == "sitemapindex":
            for loc in root.findall("{*}sitemap/{*}loc"):
                if (
                    loc.text
                    and source.allows(loc.text)
                    and source.product_sitemap in loc.text
                ):
                    pending.append(loc.text.strip())
        elif kind == "urlset":
            for entry in root.findall("{*}url"):
                page = entry.findtext("{*}loc")
                if (
                    page
                    and source.allows(page)
                    and source.product_path in urlsplit(page).path
                ):
                    pages[page.strip()] = entry.findtext("{*}lastmod")
    return pages


def _file_index_pages(
    client: PoliteClient, index: FileIndex, source: Source
) -> list[str]:
    projects: set[str] = set()
    for page in index.indexes:
        logger.info("Project index %s", page)
        for anchor in anchors(client.get(page).text):
            project = _published_id(page, anchor.href, index.project_path, source)
            if project:
                projects.add(project)
    return sorted(projects)


def _file_index_links(
    client: PoliteClient, page: str, html: str, index: FileIndex, source: Source
) -> list[Listing]:
    """A project's PDF attachments, through their information pages."""
    found: list[Listing] = []
    for anchor in anchors(html):
        info = _published_id(page, anchor.href, index.file_info_path, source)
        if info is None or not anchor.label.casefold().endswith(".pdf"):
            continue
        if source.excluded and source.excluded.search(anchor.label):
            continue
        downloads = {
            link
            for item in anchors(client.get(info).text)
            if (link := _published_id(info, item.href, index.download_path, source))
        }
        if len(downloads) == 1:
            found.append(Listing(downloads.pop(), page, anchor.label))
    return found


def discover(
    client: PoliteClient,
    source: Source,
    known: Mapping[str, PageState] | None = None,
    limit: int | None = None,
    spent: Callable[[], bool] = lambda: False,
    on_listing: Callable[[Listing], None] = lambda listing: None,
) -> Discovery:
    """Finds a publisher's build documents.

    Args:
        client: The polite client.
        source: The publisher.
        known: Each page as the last run read it; a product page whose
            `<lastmod>` hasn't changed isn't fetched again.
        limit: Read at most this many listing pages (a sample).
        spent: Whether the run's time is spent; the pages left keep what
            the last run found on them, and wait for the next run.
        on_listing: Called with each document as it's first found, so it
            can be read while discovery goes on.

    Returns:
        The documents found, each page's state, and pages that failed.
    """
    known = known or {}
    result = Discovery()

    def add(listing: Listing) -> None:
        # Checked here, not as a page is read, so the rules also apply to
        # the documents an unchanged page linked last time.
        if not source.is_build(listing.document):
            return
        if listing.document not in result.listings:
            result.listings[listing.document] = listing
            on_listing(listing)

    pages: dict[str, str | None]
    if source.file_index:
        pages = dict.fromkeys(_file_index_pages(client, source.file_index, source))
    elif source.sitemap:
        pages = _sitemap_pages(client, source)
    else:
        pages = {}
    pages.update({p.url: None for p in source.document_pages})
    # Pages never read, then those read longest ago, so a run out of time
    # carries on where the last one stopped.
    ordered = sorted(
        pages.items(),
        key=lambda item: (known[item[0]].read if item[0] in known else "", item[0]),
    )
    # A sample takes pages spread across the list: the first few alone can be
    # parts and kits with no documents (PedalPCB's are capacitors).
    chosen = (
        ordered
        if limit is None
        else [
            ordered[i * len(ordered) // limit] for i in range(min(limit, len(ordered)))
        ]
    )
    logger.info(
        "%d listing pages%s",
        len(pages),
        f"; reading {len(chosen)} spread across them (sample)"
        if limit is not None
        else "",
    )
    for number, (page, lastmod) in enumerate(chosen, 1):
        before = known.get(page)
        if spent():
            result.complete = False
            left = chosen[number - 1 :]
            logger.warning(
                "Time budget spent: %d listing pages left for the next run",
                len(left),
            )
            for later, _ in left:
                if later in known:
                    result.pages[later] = known[later]
                    for listing in known[later].listings:
                        add(listing)
            break
        if before and lastmod and before.lastmod == lastmod:
            logger.info(
                "[%d/%d] %s unchanged since %s; %d documents as before",
                number,
                len(chosen),
                page,
                lastmod,
                len(before.listings),
            )
            state = before
        else:
            try:
                html = client.get(page).text
            # A page failing is reported, and the rest go on.
            except Exception as error:
                result.failures[page] = str(error)
                logger.warning(
                    "[%d/%d] %s not read: %s", number, len(chosen), page, error
                )
                # Its documents are kept as last read: they aren't known to
                # be gone, and not adding them would remove their builds.
                if before:
                    result.pages[page] = before
                    for listing in before.listings:
                        add(listing)
                continue
            links: Iterable[Listing] = (
                _file_index_links(client, page, html, source.file_index, source)
                if source.file_index
                else pdf_links(page, html, source)
            )
            state = PageState(
                lastmod,
                tuple(links),
                datetime.now(UTC).isoformat(timespec="seconds"),
            )
            logger.info(
                "[%d/%d] %s: %d build documents",
                number,
                len(chosen),
                page,
                len(state.listings),
            )
        result.pages[page] = state
        for listing in state.listings:
            add(listing)
    return result
