"""Where each publisher lists its build documents, and which links are them.

The selection rules are the ones the corpus was gathered with: which pages
list documents, and which link labels and file names mark a build document
rather than a datasheet, artwork or drilling template. A document the rules
let through but the parser can't read cleanly is still left out of the
catalog (`build.clean`).

Aion FX is left out: its terms forbid using the site "to spider, crawl, or
scrape" (checked 2026-10-29). Builders can still add its documents by link.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class Publisher(StrEnum):
    """The publishers in the catalog."""

    PEDALPCB = "pedalpcb"
    GUITARPCB = "guitarpcb"
    TONEPAD = "tonepad"
    FIVECATS = "fivecats"


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """A page that lists build documents directly.

    Attributes:
        url: The page.
        label: Link text a build document's link must match, or None when
            every PDF on the page is one (a dedicated list).
    """

    url: str
    label: re.Pattern[str] | None = None


@dataclass(frozen=True, slots=True)
class FileIndex:
    """A site listing projects, whose attachments name their downloads.

    Tonepad links each project's files to an information page that names the
    download. Only published links are followed; numeric ids are never
    guessed.

    Attributes:
        indexes: The pages listing projects.
        project_path: A project page's path.
        file_info_path: An attachment's information page's path.
        download_path: The download's path.
    """

    indexes: tuple[str, ...]
    project_path: str
    file_info_path: str
    download_path: str


@dataclass(frozen=True, slots=True)
class Source:
    """One publisher's site, and how to find its build documents there.

    Attributes:
        publisher: The publisher.
        site: The site's home page; its domain and subdomains are allowed.
        sitemap: The sitemap index listing product pages, if it has one.
        product_sitemap: Part of a product sitemap's path, so discovery never
            walks forum posts or user profiles.
        product_path: Part of a product page's path ('/product/').
        document_pages: Pages listing documents directly.
        file_index: A project index, for a site without product sitemaps.
        legacy_hosts: Other hosts the publisher's older documents are on.
        excluded: Link text or file names that mark something other than a
            build document.
        product_label: Link text a document link on a product page must
            match, if any.
        product_row_label: A table row heading that marks the row's link as a
            build document, as an alternative to `product_label`.
        visit_page_first: Fetch the listing page before its document, as the
            site asks (Tonepad answers a download warning otherwise).
        documents_apart: The documents are on another host than the pages
            (PedalPCB's docs.pedalpcb.com), so they're read while discovery
            goes on, each host at full spacing.
    """

    publisher: Publisher
    site: str
    sitemap: str | None
    product_sitemap: str = ""
    product_path: str = "/product/"
    document_pages: tuple[DocumentPage, ...] = ()
    file_index: FileIndex | None = None
    legacy_hosts: tuple[str, ...] = ()
    excluded: re.Pattern[str] | None = None
    product_label: re.Pattern[str] | None = None
    product_row_label: re.Pattern[str] | None = None
    visit_page_first: bool = False
    documents_apart: bool = False

    def allows(self, url: str) -> bool:
        """Whether a URL is on this publisher's site."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        domain = (urlsplit(self.site).hostname or "").removeprefix("www.")
        return parts.scheme in {"http", "https"} and (
            host == domain or host.endswith("." + domain) or host in self.legacy_hosts
        )


SOURCES: dict[Publisher, Source] = {
    Publisher.PEDALPCB: Source(
        Publisher.PEDALPCB,
        site="https://www.pedalpcb.com/",
        sitemap="https://www.pedalpcb.com/sitemap_index.xml",
        product_sitemap="product-sitemap",
        documents_apart=True,
    ),
    Publisher.GUITARPCB: Source(
        Publisher.GUITARPCB,
        site="https://guitarpcb.com/",
        sitemap="https://guitarpcb.com/wp-sitemap.xml",
        product_sitemap="wp-sitemap-posts-product-",
        # Product pages also link reference guides and artwork, so their
        # links must say they're build documents; the dedicated lists'
        # links are often just project names.
        document_pages=(
            DocumentPage(
                "https://guitarpcb.com/community/topic/pedal-building-guides-mandatory-reading/",
                re.compile(r"build\s+doc", re.IGNORECASE),
            ),
            DocumentPage("https://guitarpcb.com/legacy-pcb-document-list/"),
            DocumentPage(
                "https://guitarpcb.com/community/topic/direct-build-document-link-list/"
            ),
            DocumentPage("https://guitarpcb.com/bonus-build-documents/"),
        ),
        legacy_hosts=("guitarpcb.webs.com", "www.guitarpcb.webs.com"),
        excluded=re.compile(
            r"artwork|final[\s_-]*art|drill[\s_-]*template|datasheet", re.IGNORECASE
        ),
        product_label=re.compile(r"build[\s_-]+(?:doc|instruction)", re.IGNORECASE),
    ),
    Publisher.TONEPAD: Source(
        Publisher.TONEPAD,
        site="https://tonepad.com/",
        sitemap=None,
        product_path="/project.asp",
        file_index=FileIndex(
            indexes=(
                "https://tonepad.com/projects.asp?projectType=fx",
                "https://tonepad.com/projects.asp?projectType=amp",
            ),
            project_path="/project.asp",
            file_info_path="/getFileInfo.asp",
            download_path="/getFile.asp",
        ),
        excluded=re.compile(
            r"schematics?|offboard[\s_-]*wiring|docksplitter", re.IGNORECASE
        ),
        visit_page_first=True,
    ),
    Publisher.FIVECATS: Source(
        Publisher.FIVECATS,
        site="https://www.five-cats-pedals.co.uk/",
        sitemap="https://www.five-cats-pedals.co.uk/sitemap_index.xml",
        product_sitemap="product-sitemap",
        # Titles come before the attachment's kind, so match within the
        # text. Never just 'build': that admits general guides too.
        product_label=re.compile(
            r"\bbuild[\s_-]+(?:docs?|documentation)\b", re.IGNORECASE
        ),
        product_row_label=re.compile(
            r"^build\s+(?:docs?|documentation)$", re.IGNORECASE
        ),
        excluded=re.compile(
            r"(?:^|[\W_])guide(?:[\W_]|$)|schematic|datasheet|artwork|drill[\s_-]*template",
            re.IGNORECASE,
        ),
    ),
}
