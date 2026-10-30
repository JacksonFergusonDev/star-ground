"""Reads each discovered document and keeps what it derived.

For each document the job fetches it (conditionally, after the first time),
reads it as an upload would be read, with OCR for image parts lists, and
keeps the result only when it reads cleanly (`clean`). The catalog holds:

- `builds/<publisher>/<id>.json`: an `Entry` per listed document.
- `state/<publisher>.json`: what the last runs saw (each page's
  `<lastmod>`, each document's hash, `ETag` and outcome), so the next run asks
  only about what may have changed, and documents checked longest ago go
  first when a run's time is short.

The document's bytes are read in memory and dropped.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.api import offered, read_document, read_parts
from src.bom_lib import (
    FileUpload,
    InputMethod,
    OcrVerdict,
    ParseResult,
    ParseWarningKind,
)
from src.catalog.client import (
    BlockedError,
    DisallowedError,
    GoneError,
    PoliteClient,
)
from src.catalog.discover import Listing, PageState, discover
from src.catalog.report import Event, Outcome, Reporter
from src.catalog.sources import Source

# Self-check warnings that describe the document rather than a doubtful
# reading: the builder's choice of a value, a footnote to read.
_INFORMATIONAL = {
    ParseWarningKind.VALUE_CHOICE,
    ParseWarningKind.FOOTNOTE,
    ParseWarningKind.OCR_READ,
}


@dataclass(frozen=True, slots=True)
class DocumentState:
    """A document as the last run that fetched it left it.

    Attributes:
        sha256: Its hash, if it was read.
        etag: Its `ETag`, to ask whether it changed.
        last_modified: Its `Last-Modified`, likewise.
        checked: When it was last asked about (ISO 8601).
        outcome: What became of it then.
        reason: Why, for anything but success.
        entry: Its entry's id, when it's listed.
    """

    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    checked: str = ""
    outcome: str = ""
    reason: str = ""
    entry: str | None = None


@dataclass(slots=True)
class State:
    """Everything a publisher's last runs saw."""

    pages: dict[str, PageState] = field(default_factory=dict)
    documents: dict[str, DocumentState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        """Reads a publisher's state, or starts afresh."""
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            pages={
                url: PageState(
                    page["lastmod"],
                    tuple(Listing(**listing) for listing in page["listings"]),
                )
                for url, page in raw["pages"].items()
            },
            documents={
                url: DocumentState(**document)
                for url, document in raw["documents"].items()
            },
        )

    def save(self, path: Path) -> None:
        """Writes the state, sorted so a run's changes diff cleanly."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "pages": {url: asdict(page) for url, page in sorted(self.pages.items())},
            "documents": {url: asdict(d) for url, d in sorted(self.documents.items())},
        }
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def clean(result: ParseResult) -> str | None:
    """Why a reading isn't clean enough to list, or None when it is.

    Clean means no reading errors, parts in every build, and no self-check
    warnings beyond informational ones. An image parts list counts only when
    OCR judged its reading complete.
    """
    if result.stats["errors"]:
        return result.stats["errors"][0].message
    if (
        result.ocr is not None
        and result.ocr.has_parts
        and result.ocr.verdict != OcrVerdict.COMPLETE
    ):
        return f"OCR's reading of the parts list: {result.ocr.verdict}"
    builds = [result.select(v.name) for v in result.variants] or [result]
    for build in builds:
        if not build.inventory:
            return "no parts were read"
        doubts = [w for w in build.stats["warnings"] if w.kind not in _INFORMATIONAL]
        if doubts:
            return f"{len(doubts)} self-check warning(s), first: {doubts[0].kind}: {doubts[0].message}"
    return None


def entry_id(listing: Listing) -> str:
    """A document's id: its published file name, as a URL slug."""
    stem = re.sub(r"\.pdf$", "", listing.name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "document"


def entry(
    source: Source, listing: Listing, digest: str, result: ParseResult, checked: str
) -> dict[str, Any]:
    """A listed document's catalog entry: only what was derived from it."""
    builds: list[tuple[str | None, ParseResult]] = [
        (v.name, result.select(v.name)) for v in result.variants
    ] or [(None, result)]
    return {
        "id": entry_id(listing),
        "publisher": source.publisher.value,
        "title": result.title or entry_id(listing),
        "document": listing.document,
        "page": listing.page,
        "file_name": listing.name,
        "sha256": digest,
        "checked": checked,
        "read_by_ocr": result.ocr is not None and result.ocr.has_parts,
        "builds": [
            {
                "name": name,
                "parts": [
                    {
                        "ref": part.ref,
                        "category": part.category.value,
                        "value": part.value,
                        "location": asdict(part.location) if part.location else None,
                    }
                    for part in read_parts(build)
                ],
            }
            for name, build in builds
        ],
    }


@dataclass(frozen=True, slots=True)
class Budget:
    """How long a run may keep reading documents.

    Attributes:
        deadline: When to stop starting new documents (`clock` seconds).
        clock: The current time.
    """

    deadline: float
    clock: Callable[[], float] = time.monotonic

    def spent(self) -> bool:
        """Whether the run is out of time."""
        return self.clock() >= self.deadline


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build(
    client: PoliteClient,
    source: Source,
    out: Path,
    reporter: Reporter,
    budget: Budget,
    page_limit: int | None = None,
    document_limit: int | None = None,
    read: Callable[[FileUpload], ParseResult | None] | None = None,
) -> None:
    """Brings a publisher's part of the catalog up to date.

    Args:
        client: The polite client.
        source: The publisher.
        out: The catalog's directory.
        reporter: Where progress goes.
        budget: When to stop starting new documents.
        page_limit: Read at most this many listing pages (a sample run).
        document_limit: Read at most this many documents (a sample run).
        read: Reads a document (tests pass a fake); by default, as an upload
            with OCR.
    """
    read = read or (
        lambda upload: read_document(InputMethod.UPLOAD_FILE, upload, ocr=True)
    )
    publisher = source.publisher.value
    state_path = out / "state" / f"{publisher}.json"
    builds_dir = out / "builds" / publisher
    state = State.load(state_path)

    with reporter.section(f"{publisher}: finding build documents"):
        found = discover(client, source, state.pages, page_limit)
        for page, reason in sorted(found.failures.items()):
            reporter.page_failed(publisher, page, reason)
        reporter.info(
            f"{len(found.listings)} build documents linked from {len(found.pages)} pages "
            f"({len(found.failures)} pages not read)"
        )
        state.pages = (
            found.pages if page_limit is None else {**state.pages, **found.pages}
        )

    def record(listing_url: str, document: DocumentState, event: Event) -> None:
        state.documents[listing_url] = document
        state.save(state_path)
        reporter.outcome(event)

    # A sample run doesn't see every page, so it removes nothing.
    if page_limit is None:
        with reporter.section(f"{publisher}: documents no longer linked"):
            gone = [url for url in state.documents if url not in found.listings]
            for url in gone:
                previous = state.documents.pop(url)
                if previous.entry:
                    (builds_dir / f"{previous.entry}.json").unlink(missing_ok=True)
                reporter.outcome(
                    Event(
                        publisher,
                        url,
                        Outcome.REMOVED,
                        "no longer linked from the site",
                    )
                )
            if not gone:
                reporter.info("none")
            state.save(state_path)

    listings = sorted(
        found.listings.values(),
        key=lambda li: (
            state.documents.get(li.document, DocumentState()).checked,
            li.document,
        ),
    )
    if document_limit is not None:
        listings = listings[:document_limit]
    with reporter.section(f"{publisher}: reading {len(listings)} documents"):
        for number, listing in enumerate(listings, 1):
            url = listing.document
            previous = state.documents.get(url, DocumentState())
            if budget.spent():
                reporter.outcome(
                    Event(
                        publisher,
                        url,
                        Outcome.POSTPONED,
                        "the run's time budget is spent",
                    )
                )
                continue
            reporter.info(f"[{number}/{len(listings)}] {listing.name}")
            checked = _now()
            try:
                if source.visit_page_first:
                    client.get(listing.page)
                fetched = client.get(url, previous.etag, previous.last_modified)
            except BlockedError as error:
                record(
                    url,
                    replace(
                        previous,
                        checked=checked,
                        outcome=Outcome.BLOCKED,
                        reason=str(error),
                    ),
                    Event(publisher, url, Outcome.BLOCKED, str(error)),
                )
                continue
            except GoneError as error:
                # A dead link hides the build.
                if previous.entry:
                    (builds_dir / f"{previous.entry}.json").unlink(missing_ok=True)
                record(
                    url,
                    replace(
                        previous,
                        checked=checked,
                        outcome=Outcome.GONE,
                        reason=str(error),
                        entry=None,
                    ),
                    Event(publisher, url, Outcome.GONE, str(error)),
                )
                continue
            except DisallowedError as error:
                record(
                    url,
                    replace(
                        previous,
                        checked=checked,
                        outcome=Outcome.DISALLOWED,
                        reason=str(error),
                    ),
                    Event(publisher, url, Outcome.DISALLOWED, str(error)),
                )
                continue
            except Exception as error:  # A failed fetch is reported; the rest go on.
                reason = f"couldn't fetch it: {error}"
                record(
                    url,
                    replace(
                        previous, checked=checked, outcome=Outcome.FAILED, reason=reason
                    ),
                    Event(publisher, url, Outcome.FAILED, reason),
                )
                continue
            if fetched.status == 304:
                record(
                    url,
                    replace(previous, checked=checked),
                    Event(publisher, url, Outcome.UNCHANGED, "304 Not Modified"),
                )
                continue
            digest = sha256(fetched.content).hexdigest()
            fetched_state = replace(
                previous,
                sha256=digest,
                etag=fetched.etag,
                last_modified=fetched.last_modified,
                checked=checked,
            )
            if digest == previous.sha256 and previous.outcome in (
                Outcome.LISTED,
                Outcome.UPDATED,
                Outcome.UNCHANGED,
                Outcome.NOT_CLEAN,
            ):
                record(
                    url,
                    replace(fetched_state, outcome=previous.outcome),
                    Event(publisher, url, Outcome.UNCHANGED, "same hash as last time"),
                )
                continue
            try:
                reading = read(FileUpload(listing.name, fetched.content))
                if reading is None:
                    raise ValueError("not a document Star Ground reads")
                ocr = reading.ocr
                accepted = offered(
                    reading,
                    use_ocr=ocr is not None and ocr.verdict == OcrVerdict.COMPLETE,
                )
                why = clean(replace(accepted, ocr=ocr))
            except Exception as error:  # A reading bug is reported; the rest go on.
                reason = f"couldn't read it: {type(error).__name__}: {error}"
                record(
                    url,
                    replace(
                        fetched_state, outcome=Outcome.FAILED, reason=reason, entry=None
                    ),
                    Event(publisher, url, Outcome.FAILED, reason),
                )
                continue
            if why is not None:
                if previous.entry:
                    (builds_dir / f"{previous.entry}.json").unlink(missing_ok=True)
                record(
                    url,
                    replace(
                        fetched_state, outcome=Outcome.NOT_CLEAN, reason=why, entry=None
                    ),
                    Event(publisher, url, Outcome.NOT_CLEAN, why),
                )
                continue
            listed = entry(source, listing, digest, accepted, checked)
            taken = builds_dir / f"{listed['id']}.json"
            if taken.exists() and json.loads(taken.read_text())["document"] != url:
                # Two documents published under one file name.
                listed["id"] += "-" + sha256(url.encode()).hexdigest()[:6]
            builds_dir.mkdir(parents=True, exist_ok=True)
            (builds_dir / f"{listed['id']}.json").write_text(
                json.dumps(listed, indent=1) + "\n"
            )
            outcome = Outcome.UPDATED if previous.entry else Outcome.LISTED
            reason = (
                "the document changed since it was listed; check it"
                if previous.entry
                else ""
            )
            record(
                url,
                replace(
                    fetched_state, outcome=outcome, reason=reason, entry=listed["id"]
                ),
                Event(publisher, url, outcome, reason),
            )
