"""Finds a publisher's build documents from the pages that list them.

Discovery reads only HTML and XML: sitemaps, product pages and document
lists. It never requests a document. A product page the sitemap says hasn't
changed since the last run (`<lastmod>`) isn't fetched again; its links are
taken from the last run (`PageState`).

A sitemap doesn't say what's gone: PedalPCB's is split into pages that
overlap and leave out a different few hundred products each night. So a page
an earlier run read is visited even when the sitemap no longer lists it, and
its documents are dropped only when the page itself is gone (404 or 410) or
no longer links them.

A page also says what it sells (`page_details`): the product's name, for a
document that prints none, and the publisher's categories for it, from which
the catalog says what kind of effect each build is (`effects`).
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import (
    SplitResult,
    parse_qs,
    unquote,
    urljoin,
    urlsplit,
    urlunsplit,
)
from xml.etree import ElementTree

from star_ground.catalog.client import RETRIES, GoneError, PoliteClient
from star_ground.catalog.sources import DocumentPage, FileIndex, Source

logger = logging.getLogger(__name__)

# What a page read is taken from it. A page read by an older version (or
# none: read before pages gave their names and categories) is read again,
# though the sitemap says it hasn't changed.
PAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class Listing:
    """A build document, as a publisher's page links it.

    Attributes:
        document: The document's URL.
        page: The page that links it: the product page to buy the board
            from, or a list of documents.
        name: The document's file name, as published.
        title: What the page calls it: the product's or project's name, or
            on a list of documents the link's text. None if it says nothing.
        categories: The publisher's categories for the product, as the page
            names them ('fuzz', 'amp-in-a-box', 'tremolo').
    """

    document: str
    page: str
    name: str
    title: str | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageState:
    """A listing page as the last run read it.

    Attributes:
        lastmod: The sitemap's `<lastmod>` for it then, if it gave one.
        listings: The documents it linked.
        postponed: Whether the last run ran out of time before reading it,
            so the next one reads it first.
        version: The `PAGE_VERSION` it was read with.
    """

    lastmod: str | None
    listings: tuple[Listing, ...]
    postponed: bool = False
    version: int = PAGE_VERSION


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


@dataclass(frozen=True, slots=True)
class PageDetails:
    """What a product or project page says about what it sells.

    Attributes:
        title: The product's or project's name, if the page names it.
        categories: The publisher's categories for it, as the page names them.
    """

    title: str | None
    categories: tuple[str, ...]


# Tonepad lists related projects under 'More tremolo projects:', and projects
# of the same difficulty under 'More INTERMEDIATE level projects:'.
_RELATED = re.compile(r"More (?!.*\blevel\b)(.+?) projects:", re.IGNORECASE)


class _Details(HTMLParser):
    """A page's name for its product, and the categories it files it under.

    WooCommerce (PedalPCB, GuitarPCB, Five Cats) names the product in the
    page's first `<h1>`, gives the product's own element its categories as
    `product_cat-<slug>` classes (beside `post-<id>`, where the page's
    `<body>` says `postid-<id>`), and links its main category in the
    breadcrumb. Tonepad names a project in its `projectLabel`, and its kind of
    effect in the link to related projects.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] | None = None
        self.categories: list[str] = []
        self._post: str | None = None
        self._open: list[str] = []
        self._in_title = False
        self._breadcrumb = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "body":
            self._post = next(
                (c.removeprefix("postid-") for c in classes if c.startswith("postid-")),
                None,
            )
        elif self._post and f"post-{self._post}" in classes:
            self.categories += [
                c.removeprefix("product_cat-")
                for c in classes
                if c.startswith("product_cat-")
            ]
        if tag == "nav" and "woocommerce-breadcrumb" in classes:
            self._breadcrumb = True
        elif tag == "a" and self._breadcrumb:
            path = urlsplit(attributes.get("href") or "").path
            if "/product-category/" in path:
                self.categories.append(PurePosixPath(path).name)
        if self.title is None and (
            tag == "h1" or (tag == "span" and "projectLabel" in classes)
        ):
            self.title = []
            self._in_title = True
            self._open.append(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is not None:
            self.title.append(data)
        related = _RELATED.search(data)
        if related:
            self.categories.append(related[1].strip().lower())

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            self._breadcrumb = False
        if self._open and tag == self._open[-1]:
            self._open.pop()
            self._in_title = False


def page_details(html: str) -> PageDetails:
    """What a product or project page says about what it sells.

    Args:
        html: The page.

    Returns:
        The product's name, if the page names one, and its categories, each
        once in the order the page gives them.
    """
    parser = _Details()
    parser.feed(html)
    title = " ".join(" ".join(parser.title or []).split())
    return PageDetails(title or None, tuple(dict.fromkeys(parser.categories)))


def _title(listed: DocumentPage | None, label: str, details: PageDetails) -> str | None:
    """What a page calls a document it links.

    A product page names its product. A dedicated list of documents names
    each in its link ('*PURP': the asterisk marks a new one); a page whose
    links must say they're build documents ('Build Document (PDF)') and is
    headed by something else names none.
    """
    if listed is None:
        return details.title
    if listed.label is None:
        return " ".join(label.lstrip("*").split()) or None
    return None


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
    details = page_details(html)
    on_product = source.product_path in urlsplit(page).path
    label_rule = (
        listed.label if listed else (source.product_label if on_product else None)
    )
    found: dict[str, Listing] = {}
    for anchor in anchors(html):
        url = _link(page, anchor.href.strip().strip("\"'"))
        if url is None:
            continue
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
        found.setdefault(
            document,
            Listing(
                document,
                page,
                _name(document),
                _title(listed, anchor.label, details),
                details.categories,
            ),
        )
    if listed is None and len(found) > 1:
        # A bundle's page ('EQD Mega Pack') names the bundle, not each board.
        return [replace(listing, title=None) for listing in found.values()]
    return list(found.values())


def _link(base: str, href: str) -> SplitResult | None:
    """A link on a page, resolved; None, and a note in the log, if it's malformed.

    One bad link ('http://[oops/x.pdf') is skipped, not the page around it.
    """
    try:
        return urlsplit(urljoin(base, href))
    except ValueError as error:
        logger.info("%s links %r, which isn't a URL (%s); skipped", base, href, error)
        return None


def _published_id(base: str, href: str, path: str, source: Source) -> str | None:
    """A published numeric-id link, normalized; ids are never guessed."""
    url = _link(base, href)
    if url is None:
        return None
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


def _sitemap(client: PoliteClient, url: str, source: Source) -> ElementTree.Element:
    """A sitemap, asked for again when what comes back isn't XML.

    PedalPCB has answered its sitemap index with a 200 and a page that isn't
    one, and a moment later with the sitemap.
    """
    for attempt in range(RETRIES):
        try:
            return ElementTree.fromstring(client.get(url, allows=source.allows).content)
        except ElementTree.ParseError as error:
            client.back_off(attempt, f"{url} didn't answer with a sitemap ({error})")
    content = client.get(url, allows=source.allows).content
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        title = re.search(rb"<title[^>]*>([^<]*)", content, re.IGNORECASE)
        answered = (
            f"a page titled {title[1].decode(errors='replace').strip()!r}"
            if title
            else f"something beginning {content[:80]!r}"
        )
        raise ValueError(
            f"{url} didn't answer with a sitemap in {RETRIES + 1} tries, "
            f"but with {answered} ({error})"
        ) from None


def _when(lastmod: str) -> datetime | None:
    """A `<lastmod>` as a time, in UTC when it gives no zone; None if unreadable."""
    try:
        when = datetime.fromisoformat(lastmod.strip())
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _newest(a: str | None, b: str | None) -> str | None:
    """The later of two `<lastmod>`s; either, when one is missing or unreadable."""
    if a is None or b is None:
        return a or b
    when_a, when_b = _when(a), _when(b)
    if when_a is None or when_b is None:
        return b
    return b if when_b > when_a else a


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
        root = _sitemap(client, url, source)
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
                    # PedalPCB lists some products twice, with different
                    # dates: the newer one says when it last changed.
                    page = page.strip()
                    pages[page] = _newest(pages.get(page), entry.findtext("{*}lastmod"))
    return pages


def _file_index_pages(
    client: PoliteClient, index: FileIndex, source: Source
) -> list[str]:
    projects: set[str] = set()
    for page in index.indexes:
        logger.info("Project index %s", page)
        for anchor in anchors(client.get(page, allows=source.allows).text):
            project = _published_id(page, anchor.href, index.project_path, source)
            if project:
                projects.add(project)
    return sorted(projects)


def _file_index_links(
    client: PoliteClient, page: str, html: str, index: FileIndex, source: Source
) -> list[Listing]:
    """A project's PDF attachments, through their information pages.

    An attachment whose information page is gone is skipped. Any other
    failure fails the project's page, so it keeps the documents it linked
    last time rather than losing one to a passing error.
    """
    details = page_details(html)
    found: list[Listing] = []
    for anchor in anchors(html):
        info = _published_id(page, anchor.href, index.file_info_path, source)
        if info is None or not anchor.label.casefold().endswith(".pdf"):
            continue
        if source.excluded and source.excluded.search(anchor.label):
            continue
        try:
            info_page = client.get(info, allows=source.allows).text
        except GoneError as error:
            logger.info("%s: %s; %s skipped", page, error, anchor.label)
            continue
        downloads = {
            link
            for item in anchors(info_page)
            if (link := _published_id(info, item.href, index.download_path, source))
        }
        if len(downloads) == 1:
            found.append(
                Listing(
                    downloads.pop(),
                    page,
                    anchor.label,
                    details.title,
                    details.categories,
                )
            )
    return found


def _listing_pages(
    client: PoliteClient, source: Source, known: Mapping[str, PageState]
) -> list[tuple[str, str | None]]:
    """Every listing page to consider, with its `<lastmod>`, in the order to read.

    Pages never read and those the last run ran out of time for go first, so
    a run out of time carries on where the last one stopped.
    """
    pages: dict[str, str | None]
    if source.file_index:
        pages = dict.fromkeys(_file_index_pages(client, source.file_index, source))
    elif source.sitemap:
        pages = _sitemap_pages(client, source)
    else:
        pages = {}
    pages.update({p.url: None for p in source.document_pages})
    unlisted = [page for page in known if page not in pages]
    if unlisted:
        logger.info(
            "%d pages read before aren't listed now; visiting them", len(unlisted)
        )
    pages.update(dict.fromkeys(unlisted))
    return sorted(
        pages.items(),
        key=lambda item: (
            item[0] in known and not known[item[0]].postponed,
            item[0],
        ),
    )


def _sample[T](items: list[T], limit: int | None) -> list[T]:
    """At most `limit` items spread evenly across a list, each once; all for None.

    The first few alone can be parts and kits with no documents (PedalPCB's
    are capacitors), so a sample is taken across the whole list.
    """
    if limit is None or limit >= len(items):
        return items
    return [items[i * len(items) // limit] for i in range(limit)]


def _unchanged(before: PageState, lastmod: str | None) -> bool:
    """Whether a page needn't be read: the sitemap gives no later date for it.

    Only a later date is a change: one the same or earlier is the sitemap
    listing the page from an older copy.
    """
    return bool(
        before.version >= PAGE_VERSION
        and lastmod
        and _newest(before.lastmod, lastmod) == before.lastmod
    )


def _read_page(
    client: PoliteClient,
    source: Source,
    page: str,
    lastmod: str | None,
    before: PageState | None,
) -> PageState:
    """Reads a listing page for the documents it links.

    Raises:
        GoneError: The page is gone.
        Exception: Anything else that kept it, or a page it leads to, from
            being read.
    """
    html = client.get(page, allows=source.allows).text
    links: Iterable[Listing] = (
        _file_index_links(client, page, html, source.file_index, source)
        if source.file_index
        else pdf_links(page, html, source)
    )
    # One the sitemap left out keeps its `<lastmod>`, so it isn't read again
    # when the sitemap lists it unchanged.
    return PageState(lastmod or (before.lastmod if before else None), tuple(links))


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
            `<lastmod>` hasn't changed isn't fetched again, and one the
            sitemap no longer lists is still visited.
        limit: Read at most this many listing pages (a sample).
        spent: Whether the run's time is spent, or the run is stopping; the
            pages left keep what the last run found on them, and wait for
            the next run.
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
        first = result.listings.get(listing.document)
        if first is None:
            result.listings[listing.document] = listing
            on_listing(listing)
        else:
            # GuitarPCB lists a document and sells its board, and Five Cats
            # sells it alone and in bundles: each page may say something
            # the other doesn't.
            result.listings[listing.document] = replace(
                first,
                title=first.title or listing.title,
                categories=tuple(dict.fromkeys(first.categories + listing.categories)),
            )

    def keep(page: str, state: PageState) -> None:
        result.pages[page] = state
        for listing in state.listings:
            add(listing)

    ordered = _listing_pages(client, source, known)
    chosen = _sample(ordered, limit)
    logger.info(
        "%d listing pages%s",
        len(ordered),
        f"; reading {len(chosen)} spread across them (sample)"
        if limit is not None
        else "",
    )
    for number, (page, lastmod) in enumerate(chosen, 1):
        before = known.get(page)
        step = f"[{number}/{len(chosen)}]"
        if spent():
            result.complete = False
            left = chosen[number - 1 :]
            logger.warning(
                "Time budget spent: %d listing pages left for the next run",
                len(left),
            )
            for later, _ in left:
                if later in known:
                    keep(later, replace(known[later], postponed=True))
            break
        if before and _unchanged(before, lastmod):
            logger.info(
                "%s %s unchanged since %s; %d documents as before",
                step,
                page,
                before.lastmod,
                len(before.listings),
            )
            keep(page, replace(before, postponed=False))
            continue
        try:
            state = _read_page(client, source, page, lastmod, before)
        except GoneError as error:
            # Its documents are no longer linked, unless another page does.
            logger.info("%s %s", step, error)
            continue
        # A page failing is reported, and the rest go on.
        except Exception as error:
            result.failures[page] = str(error)
            logger.warning("%s %s not read: %s", step, page, error)
            # Its documents are kept as last read: they aren't known to be
            # gone, and not adding them would remove their builds.
            if before:
                keep(page, replace(before, postponed=False))
            continue
        logger.info("%s %s: %d build documents", step, page, len(state.listings))
        keep(page, state)
    return result
