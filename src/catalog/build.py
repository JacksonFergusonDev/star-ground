"""Reads each discovered document and keeps what it derived.

For each document the job fetches it (conditionally, after the first time),
reads it as an upload would be read, with OCR for image parts lists, and
keeps the result only when it reads cleanly (`clean`). The catalog holds:

- `builds/<publisher>/<id>.json`: an `Entry` per listed document, with
  where its board and schematic draw each part (`boards`, step 7).
- `state/<publisher>.json`: what the last runs saw (each page's
  `<lastmod>`, each document's hash, `ETag` and outcome), so the next run asks
  only about what may have changed, and documents checked longest ago go
  first when a run's time is short.

The document's bytes are read in memory and dropped.
"""

import json
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.api import counted_parts, offered, read_document, read_parts
from src.bom_lib import (
    FileUpload,
    InputMethod,
    OcrVerdict,
    ParseResult,
    ParseWarningKind,
)
from src.bom_lib.boards import board_marks, marks_by_ref
from src.bom_lib.types import Location
from src.catalog.client import (
    BlockedError,
    DisallowedError,
    GoneError,
    PoliteClient,
)
from src.catalog.discover import Discovery, Listing, PageState, discover
from src.catalog.report import Event, Outcome, Reporter
from src.catalog.sources import Source
from src.catalog.verified import (
    Verification,
    VerifiedList,
    builds,
    document_hash,
    fingerprint,
)

# The board finder's version. An entry whose positions came from an older
# one (or none: listed before step 7) is fetched in full once more and given
# new positions, without being read or listed again.
BOARDS_VERSION = 1

FindBoards = Callable[[bytes, list[str], list[Location]], dict[str, list[Location]]]


def find_boards(
    content: bytes, refs: list[str], listing: list[Location]
) -> dict[str, list[Location]]:
    """Where a document draws each designator outside its parts list."""
    return marks_by_ref(board_marks(content, refs, listing))


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
        sha256: Its `verified.document_hash`, if it was read.
        etag: Its `ETag`, to ask whether it changed.
        last_modified: Its `Last-Modified`, likewise.
        checked: When it was last asked about (ISO 8601).
        outcome: What became of it then.
        reason: Why, for anything but success.
        entry: Its entry's id, when it's listed.
        parts: The `verified.fingerprint` of its reading, when it was read.
        verification: Whether that reading was checked by hand, then.
        boards: The `BOARDS_VERSION` its entry's board positions came from;
            0 for none.
    """

    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    checked: str = ""
    outcome: str = ""
    reason: str = ""
    entry: str | None = None
    parts: str | None = None
    verification: str = ""
    boards: int = 0


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
                    page.get("read", ""),
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
    for _, build in builds(result):
        if not build.inventory:
            return "no parts were read"
        doubts = [w for w in build.stats["warnings"] if w.kind not in _INFORMATIONAL]
        if doubts:
            return f"{len(doubts)} self-check warning(s), first: {doubts[0].kind}: {doubts[0].message}"
    return None


def catalog_reading(reading: ParseResult) -> ParseResult:
    """A reading as the catalog lists it: OCR's parts accepted when complete.

    OCR's attempt stays on the result, so `clean` can judge it.
    """
    ocr = reading.ocr
    complete = ocr is not None and ocr.verdict == OcrVerdict.COMPLETE
    return replace(offered(reading, use_ocr=complete), ocr=ocr)


def entry_id(listing: Listing) -> str:
    """A document's id: its published file name, as a URL slug."""
    stem = re.sub(r"\.pdf$", "", listing.name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "document"


def entry(
    source: Source,
    listing: Listing,
    digest: str,
    result: ParseResult,
    checked: str,
    verified: bool,
) -> dict[str, Any]:
    """A listed document's catalog entry: only what was derived from it."""
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
        "verified": verified,
        "builds": [
            {"name": name, "parts": _entry_parts(build)}
            for name, build in builds(result)
        ],
    }


def _entry_parts(build: ParseResult) -> list[dict[str, Any]]:
    """A build's parts: each designator, then each part counted by value.

    A counted part ('2 - 47', as Tonepad lists most of a build) has no
    designator, so its `ref` is None and its `count` says how many.
    """
    designated = [
        {
            "ref": part.ref,
            "count": 1,
            "category": part.category.value,
            "value": part.value,
            "location": asdict(part.location) if part.location else None,
        }
        for part in read_parts(build)
    ]
    counted = [
        {
            "ref": None,
            "count": part.count,
            "category": part.category.value,
            "value": part.value,
            "location": None,
        }
        for part in counted_parts(build)
    ]
    return designated + counted


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


def _unchanged(
    publisher: str, url: str, previous: DocumentState, how: str, kept: Verification
) -> Event:
    """A document unchanged since the last run, reported as it stands."""
    if previous.outcome == Outcome.NOT_CLEAN:
        return Event(
            publisher, url, Outcome.NOT_CLEAN, f"unchanged ({how}): {previous.reason}"
        )
    return Event(
        publisher, url, Outcome.UNCHANGED, how, verified=kept == Verification.VERIFIED
    )


def _found_while_reading(
    source: Source,
    client: PoliteClient,
    known: dict[str, PageState],
    page_limit: int | None,
    document_limit: int | None,
    budget: Budget,
    read_one: Callable[[Listing, str], None],
) -> Discovery:
    """Discovers on one thread and reads each document as it's found.

    For a publisher whose documents are on another host than its pages
    (PedalPCB's docs.pedalpcb.com): discovery gets its own client, so each
    host keeps its own spacing and neither waits for the other.

    Returns:
        What discovery found.
    """
    found: queue.SimpleQueue[Listing | None] = queue.SimpleQueue()
    result: list[Discovery] = []
    failure: list[BaseException] = []

    def find() -> None:
        try:
            result.append(
                discover(
                    client.alongside(),
                    source,
                    known,
                    page_limit,
                    budget.spent,
                    on_listing=found.put,
                )
            )
        except BaseException as error:  # Raised again on the reading thread.
            failure.append(error)
        finally:
            found.put(None)

    finder = threading.Thread(target=find, name=f"{source.publisher}-discovery")
    finder.start()
    count = 0
    while (listing := found.get()) is not None:
        if document_limit is not None and count >= document_limit:
            continue
        count += 1
        read_one(listing, f"[{count}]")
    finder.join()
    if failure:
        raise failure[0]
    return result[0]


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
    boards: FindBoards = find_boards,
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
        boards: Finds where a document draws each part (tests pass a fake).
    """
    read = read or (
        lambda upload: read_document(InputMethod.UPLOAD_FILE, upload, ocr=True)
    )
    publisher = source.publisher.value
    state_path = out / "state" / f"{publisher}.json"
    builds_dir = out / "builds" / publisher
    state = State.load(state_path)
    verified = VerifiedList.load(out / "verified.json")

    def still(previous: DocumentState) -> Verification:
        """A reading kept from an earlier run, checked against today's list."""
        if previous.sha256 is None or previous.parts is None:
            return Verification.UNVERIFIED
        return verified.check(previous.sha256, previous.parts)

    def mark(previous: DocumentState, verification: Verification) -> None:
        """Updates a listed build's mark when the list changed under it."""
        if previous.entry and previous.verification != verification:
            path = builds_dir / f"{previous.entry}.json"
            if path.exists():
                data = json.loads(path.read_text())
                data["verified"] = verification == Verification.VERIFIED
                path.write_text(json.dumps(data, indent=1) + "\n")

    def add_boards(entry_id: str, content: bytes) -> None:
        """Adds where the document draws each of its entry's parts."""
        path = builds_dir / f"{entry_id}.json"
        data = json.loads(path.read_text())
        parts = [p for build in data["builds"] for p in build["parts"]]
        listing = [Location(**p["location"]) for p in parts if p["location"]]
        try:
            found = boards(content, [p["ref"] for p in parts if p["ref"]], listing)
        except Exception as error:  # The build is still listed, without them.
            reporter.warning(
                f"couldn't find where {entry_id} draws its parts: "
                f"{type(error).__name__}: {error}",
                title=f"{publisher}: board positions",
            )
            found = {}
        data["boards"] = {
            ref: [asdict(loc) for loc in places] for ref, places in found.items()
        }
        path.write_text(json.dumps(data, indent=1) + "\n")

    def record(listing_url: str, document: DocumentState, event: Event) -> None:
        state.documents[listing_url] = document
        state.save(state_path)
        reporter.outcome(event)

    def read_one(listing: Listing, label: str) -> None:
        """Fetches one document, reads it if it changed, and records the outcome."""
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
            return
        reporter.info(f"{label} {listing.name}")
        checked = _now()
        try:
            if source.visit_page_first:
                client.get(listing.page)
            # A document left out but since checked by hand is fetched in
            # full, so it can be read and listed.
            newly_checked = (
                previous.outcome == Outcome.NOT_CLEAN
                and previous.sha256 in verified.checked
            )
            # A listed build without current board positions is fetched in
            # full too, so they can be found.
            needs_boards = (
                previous.entry is not None and previous.boards < BOARDS_VERSION
            )
            fetched = (
                client.get(url)
                if newly_checked or needs_boards
                else client.get(url, previous.etag, previous.last_modified)
            )
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
            return
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
            return
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
            return
        except Exception as error:  # A failed fetch is reported; the rest go on.
            reason = f"couldn't fetch it: {error}"
            record(
                url,
                replace(
                    previous, checked=checked, outcome=Outcome.FAILED, reason=reason
                ),
                Event(publisher, url, Outcome.FAILED, reason),
            )
            return
        if fetched.status == 304:
            kept = still(previous)
            mark(previous, kept)
            record(
                url,
                replace(previous, checked=checked, verification=kept),
                _unchanged(publisher, url, previous, "304 Not Modified", kept),
            )
            return
        digest = document_hash(fetched.content)
        fetched_state = replace(
            previous,
            sha256=digest,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
            checked=checked,
        )
        if (
            digest == previous.sha256
            and not newly_checked
            and previous.outcome
            in (
                Outcome.LISTED,
                Outcome.UPDATED,
                Outcome.UNCHANGED,
                Outcome.NOT_CLEAN,
            )
        ):
            kept = still(previous)
            mark(previous, kept)
            how = "same hash as last time"
            added = previous.boards
            if needs_boards and previous.entry:
                add_boards(previous.entry, fetched.content)
                how += "; board positions added"
                added = BOARDS_VERSION
            record(
                url,
                replace(
                    fetched_state,
                    outcome=previous.outcome,
                    verification=kept,
                    boards=added,
                ),
                _unchanged(publisher, url, previous, how, kept),
            )
            return
        try:
            reading = read(FileUpload(listing.name, fetched.content))
            if reading is None:
                raise ValueError("not a document Star Ground reads")
            accepted = catalog_reading(reading)
            why = clean(accepted)
            parts = fingerprint(accepted)
        except Exception as error:  # A reading bug is reported; the rest go on.
            reason = f"couldn't read it: {type(error).__name__}: {error}"
            record(
                url,
                replace(
                    fetched_state, outcome=Outcome.FAILED, reason=reason, entry=None
                ),
                Event(publisher, url, Outcome.FAILED, reason),
            )
            return
        verification = verified.check(digest, parts)
        fetched_state = replace(fetched_state, parts=parts, verification=verification)
        if verification == Verification.CHANGED:
            reporter.warning(
                f"{url} was checked by hand, but today's reading of the same file "
                "differs from the one checked: likely a parser regression",
                title=f"{publisher}: verified reading changed",
            )
        is_verified = verification == Verification.VERIFIED
        # A hand check can't make a build of no parts worth listing.
        empty = any(not build.inventory for _, build in builds(accepted))
        if why is not None and is_verified and not empty:
            # Checked by hand, so its warnings were looked into.
            reporter.info(f"listed though not clean, as it was checked by hand ({why})")
        elif why is not None:
            if previous.entry:
                (builds_dir / f"{previous.entry}.json").unlink(missing_ok=True)
            record(
                url,
                replace(
                    fetched_state, outcome=Outcome.NOT_CLEAN, reason=why, entry=None
                ),
                Event(publisher, url, Outcome.NOT_CLEAN, why),
            )
            return
        listed = entry(source, listing, digest, accepted, checked, is_verified)
        taken = builds_dir / f"{listed['id']}.json"
        if taken.exists() and json.loads(taken.read_text())["document"] != url:
            # Two documents published under one file name.
            listed["id"] += "-" + sha256(url.encode()).hexdigest()[:6]
        builds_dir.mkdir(parents=True, exist_ok=True)
        (builds_dir / f"{listed['id']}.json").write_text(
            json.dumps(listed, indent=1) + "\n"
        )
        add_boards(listed["id"], fetched.content)
        outcome = Outcome.UPDATED if previous.entry else Outcome.LISTED
        reason = (
            "the document changed since it was listed; check it"
            if previous.entry
            else ""
        )
        record(
            url,
            replace(
                fetched_state,
                outcome=outcome,
                reason=reason,
                entry=listed["id"],
                boards=BOARDS_VERSION,
            ),
            Event(publisher, url, outcome, reason, verified=is_verified),
        )

    def report_discovery(found: Discovery) -> None:
        for page, reason in sorted(found.failures.items()):
            reporter.page_failed(publisher, page, reason)
        reporter.info(
            f"{len(found.listings)} build documents linked from {len(found.pages)} "
            f"pages ({len(found.failures)} pages not read)"
        )
        whole = page_limit is None and found.complete
        state.pages = found.pages if whole else {**state.pages, **found.pages}

    if source.documents_apart:
        # The documents are on another host than the pages, so each is read
        # as soon as discovery finds it, both hosts at full spacing.
        with reporter.section(f"{publisher}: finding and reading build documents"):
            found = _found_while_reading(
                source,
                client,
                state.pages,
                page_limit,
                document_limit,
                budget,
                read_one,
            )
            report_discovery(found)
    else:
        with reporter.section(f"{publisher}: finding build documents"):
            found = discover(client, source, state.pages, page_limit, budget.spent)
            report_discovery(found)
        listings = sorted(
            found.listings.values(),
            key=lambda li: (
                state.documents.get(li.document, DocumentState()).checked,
                li.document,
            ),
        )[:document_limit]
        with reporter.section(f"{publisher}: reading {len(listings)} documents"):
            for number, listing in enumerate(listings, 1):
                read_one(listing, f"[{number}/{len(listings)}]")

    # A sample, or a run out of time, doesn't see every page, so it can't
    # tell what's gone: it removes nothing.
    if page_limit is None and found.complete:
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
    state.save(state_path)
