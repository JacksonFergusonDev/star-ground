"""Reads each discovered document and keeps what it derived.

For each document the job fetches it (conditionally, once a reading of it
has settled: see `_SETTLED`), reads it as an upload would be read, with OCR
for image parts lists, and keeps the result only when it reads cleanly
(`clean`). The catalog holds:

- `builds/<publisher>/<id>.json`: a `CatalogEntry` per listed document, with
  where its board and schematic draw each part (`boards`, step 7) and what
  kind of effect it is (`effects`).
- `effects.json`, kept by hand: effects for builds whose pages say nothing.
- `state/<publisher>.json`: what the last runs saw (each page's
  `<lastmod>`, each document's hash, `ETag` and outcome), so the next run asks
  only about what may have changed, and what a run ran out of time for goes
  first in the next. It records no times, so a night that finds nothing new
  leaves it as it was and proposes no update.

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

from star_ground import version
from star_ground.api import offered, read_document
from star_ground.bom_lib import (
    FileUpload,
    InputMethod,
    OcrVerdict,
    ParseResult,
    ParseWarningKind,
)
from star_ground.bom_lib.boards import board_marks, marks_by_ref
from star_ground.bom_lib.types import Location
from star_ground.catalog.client import (
    BlockedError,
    DisallowedError,
    Fetched,
    GoneError,
    PoliteClient,
)
from star_ground.catalog.discover import Discovery, Listing, PageState, discover
from star_ground.catalog.effects import Effect, Overrides, effects
from star_ground.catalog.entries import CatalogEntry, rounded, to_json
from star_ground.catalog.files import write_atomically
from star_ground.catalog.report import Event, Outcome, Reporter
from star_ground.catalog.sources import Source
from star_ground.catalog.verified import (
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

# Outcomes after which the state holds a reading of the document as it was
# fetched, listed or judged not clean, so a 304 or the same hash means there's
# nothing new to read. After any other (gone, failed, blocked, disallowed) the
# state's reading may be missing or stale, so the document is fetched in full
# and read again: a document that comes back, unchanged, is listed again.
_SETTLED = frozenset(
    {Outcome.LISTED, Outcome.UPDATED, Outcome.UNCHANGED, Outcome.NOT_CLEAN}
)

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
        outcome: What became of it then; None before it was first asked about.
        reason: Why, for anything but success.
        entry: Its entry's id, when it's listed.
        parts: The `verified.fingerprint` of its reading, when it was read.
        verification: Whether that reading was checked by hand, then; None
            until it was read.
        boards: The `BOARDS_VERSION` its entry's board positions came from;
            0 for none.
        reader: The Star Ground version that last read it, when it was read.
        title: The title its reading found, when it was read; the entry's
            title is this, or what its page calls it (`entry_title`).
        postponed: Whether the last run ran out of time before asking about
            it, so the next one asks first.
    """

    sha256: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    outcome: Outcome | None = None
    reason: str = ""
    entry: str | None = None
    parts: str | None = None
    verification: Verification | None = None
    boards: int = 0
    reader: str = ""
    title: str | None = None
    postponed: bool = False

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> DocumentState:
        """Reads a document's state as `as_json` wrote it.

        Args:
            raw: Its member of a state file's `documents`.

        Returns:
            The document's state.
        """
        return cls(
            **{
                **raw,
                "outcome": Outcome(raw["outcome"]) if raw["outcome"] else None,
                "verification": (
                    Verification(raw["verification"]) if raw["verification"] else None
                ),
            }
        )

    def as_json(self) -> dict[str, Any]:
        """The document's state as a state file keeps it.

        An outcome or verification it doesn't have yet is written as "", as
        the state files have always kept it.

        Returns:
            Plain JSON data, a member per attribute.
        """
        return {
            **asdict(self),
            "outcome": self.outcome.value if self.outcome else "",
            "verification": self.verification.value if self.verification else "",
        }


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
                    tuple(
                        Listing(
                            **{**listing, "categories": tuple(listing["categories"])}
                        )
                        for listing in page["listings"]
                    ),
                    page["postponed"],
                    page["version"],
                )
                for url, page in raw["pages"].items()
            },
            documents={
                url: DocumentState.from_json(document)
                for url, document in raw["documents"].items()
            },
        )

    def save(self, path: Path) -> None:
        """Writes the state sorted, a page or document per line, to diff cleanly.

        It's written whole or not at all: a run killed mid-write leaves the
        last state, which the next run can load.
        """
        data = {
            "pages": {url: asdict(page) for url, page in sorted(self.pages.items())},
            "documents": {
                url: d.as_json() for url, d in sorted(self.documents.items())
            },
        }
        write_atomically(path, to_json(data, expand=2) + "\n")


def clean(result: ParseResult) -> str | None:
    """Why a reading isn't clean enough to list, or None when it is.

    Clean means no reading errors, parts in every build, and no self-check
    warnings beyond informational ones. An image parts list counts only when
    OCR judged its reading complete.
    """
    if result.stats.errors:
        return result.stats.errors[0].message
    if (
        result.ocr is not None
        and result.ocr.has_parts
        and result.ocr.verdict != OcrVerdict.COMPLETE
    ):
        return f"OCR's reading of the parts list: {result.ocr.verdict}"
    for _, build in builds(result):
        if not build.inventory:
            return "no parts were read"
        doubts = [w for w in build.stats.warnings if w.kind not in _INFORMATIONAL]
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


# Words in a file name that aren't the build's name: publishers' prefixes
# ('FCP-', 'BD_', 'tonepad_') and what kind of file it is.
_FILE_NOISE = re.compile(
    r"fcp|bd|tonepad|guitarpcb(?:\.com)?|pedalpcb|i?insert|build|document|doc|pdf",
    re.IGNORECASE,
)
# Five Cats' 'V1-0': a version, once its parts are apart.
_VERSION = re.compile(r"\b[vV](\d+) (\d)\b")


def entry_title(read: str | None, listing: Listing) -> str:
    """A build's title: the document's own, else its page's, else its file's.

    Args:
        read: The title the reading found on the document, if any.
        listing: The document as its page links it.

    Returns:
        The title: 'Albus' from Five Cats' product page for a document that
        prints its title as an image, 'Fuzzy Bee' from GuitarPCB's list, or
        'MUFFN v4 2022' from its file name.
    """
    if read:
        # GuitarPCB's PUFF v4 title ends in zero-width spaces: they're spaces.
        return " ".join(read.replace("\u200b", " ").split())
    if listing.title:
        # 'Albus - Mad Professor Simble Overdrive Clone', with any dash: the
        # name, then what it's a clone of.
        return re.split(r"\s*[\u2013\u2014-]\s+|\s+[\u2013\u2014-]\s*", listing.title)[
            0
        ]
    stem = re.sub(r"\.pdf$", "", listing.name, flags=re.IGNORECASE)
    words = [
        w for w in re.split(r"[\s_-]+", stem) if w and not _FILE_NOISE.fullmatch(w)
    ]
    return _VERSION.sub(r"v\1.\2", " ".join(words)) or entry_id(listing)


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


def _relisted(previous: DocumentState, digest: str, parts: str) -> tuple[Outcome, str]:
    """What became of a document just read and listed, and why."""
    if not previous.entry:
        return Outcome.LISTED, ""
    if digest != previous.sha256:
        return Outcome.UPDATED, "the document changed since it was listed; check it"
    if parts != previous.parts:
        return Outcome.UPDATED, "read again, and its parts changed; check it"
    return Outcome.UNCHANGED, "read again"


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

    For a publisher whose documents are mostly on another host than its
    pages (PedalPCB's docs.pedalpcb.com): discovery gets its own client
    (`PoliteClient.alongside`), so neither thread waits for the other's
    hosts, while a host both ask is still spaced and blocked as one.

    If reading fails outright, discovery is told to stop and is waited for
    before the failure goes on, so it never keeps crawling behind the next
    publisher's run.

    Returns:
        What discovery found.
    """
    found: queue.SimpleQueue[Listing | None] = queue.SimpleQueue()
    result: list[Discovery] = []
    failure: list[BaseException] = []
    stop = threading.Event()

    def find() -> None:
        try:
            result.append(
                discover(
                    client.alongside(),
                    source,
                    known,
                    page_limit,
                    lambda: stop.is_set() or budget.spent(),
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
    try:
        while (listing := found.get()) is not None:
            if document_limit is not None and count >= document_limit:
                continue
            count += 1
            read_one(listing, f"[{count}]")
    finally:
        stop.set()
        finder.join()
    if failure:
        raise failure[0]
    return result[0]


def _asked_before(document: DocumentState | None) -> bool:
    """Whether a document waits behind the new and the postponed ones."""
    return document is not None and not document.postponed


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Sample:
    """A run's limits, to try the job on a few pages and documents.

    Attributes:
        pages: Read at most this many listing pages.
        documents: Read at most this many documents.
    """

    pages: int | None = None
    documents: int | None = None


def _read_as_upload(upload: FileUpload) -> ParseResult | None:
    return read_document(InputMethod.UPLOAD_FILE, upload, ocr=True)


@dataclass
class CatalogRun:
    """One run bringing a publisher's part of the catalog up to date.

    Attributes:
        client: The polite client.
        source: The publisher.
        out: The catalog's directory.
        reporter: Where progress goes.
        budget: When to stop starting new documents.
        sample: Limits for a sample run; none for a whole one.
        read: Reads a document (tests pass a fake); by default, as an upload
            with OCR.
        locate_boards: Finds where a document draws each part (tests pass a
            fake).
    """

    client: PoliteClient
    source: Source
    out: Path
    reporter: Reporter
    budget: Budget
    sample: Sample = field(default_factory=Sample)
    read: Callable[[FileUpload], ParseResult | None] = _read_as_upload
    locate_boards: FindBoards = find_boards

    def __post_init__(self) -> None:
        """Loads the publisher's state and the hand-kept lists."""
        self.publisher = self.source.publisher.value
        self.state_path = self.out / "state" / f"{self.publisher}.json"
        self.builds_dir = self.out / "builds" / self.publisher
        self.state = State.load(self.state_path)
        self.verified = VerifiedList.load(self.out / "verified.json")
        self.overrides = Overrides.load(self.out / "effects.json")

    def run(self) -> None:
        """Finds the publisher's documents, reads each, and removes the gone."""
        publisher, reporter = self.publisher, self.reporter
        if self.source.documents_apart:
            # The documents are on another host than the pages, so each is read
            # as soon as discovery finds it, both hosts at full spacing.
            with reporter.section(f"{publisher}: finding and reading build documents"):
                found = _found_while_reading(
                    self.source,
                    self.client,
                    self.state.pages,
                    self.sample.pages,
                    self.sample.documents,
                    self.budget,
                    self.read_one,
                )
                self._report_discovery(found)
        else:
            with reporter.section(f"{publisher}: finding build documents"):
                found = discover(
                    self.client,
                    self.source,
                    self.state.pages,
                    self.sample.pages,
                    self.budget.spent,
                )
                self._report_discovery(found)
            listings = sorted(
                found.listings.values(),
                key=lambda li: (
                    _asked_before(self.state.documents.get(li.document)),
                    li.document,
                ),
            )[: self.sample.documents]
            with reporter.section(f"{publisher}: reading {len(listings)} documents"):
                for number, listing in enumerate(listings, 1):
                    self.read_one(listing, f"[{number}/{len(listings)}]")

        # A sample, or a run out of time, doesn't see every page, so it can't
        # tell what's gone: it removes nothing.
        if self.sample.pages is None and found.complete:
            with reporter.section(f"{publisher}: documents no longer linked"):
                self._remove_unlinked(found)
        self.state.save(self.state_path)

    def read_one(self, listing: Listing, label: str) -> None:
        """Fetches one document, reads it if it changed, and records the outcome."""
        url = listing.document
        previous = self.state.documents.get(url, DocumentState())
        if self.budget.spent():
            if url in self.state.documents:
                self.state.documents[url] = replace(previous, postponed=True)
            self.reporter.outcome(
                Event(
                    self.publisher,
                    url,
                    Outcome.POSTPONED,
                    "the run's time budget is spent",
                )
            )
            return
        self.reporter.info(f"{label} {listing.name}")
        checked = _now()
        checked_by_hand = previous.sha256 in self.verified.checked
        # A document left out but since checked by hand is fetched in full,
        # so it can be read and listed. Only once: if the list says the same
        # of its reading as when it was read (it was checked and still isn't
        # clean, or the reading checked differs from it), it has settled.
        newly_checked = (
            previous.outcome == Outcome.NOT_CLEAN
            and checked_by_hand
            and self._still(previous) != previous.verification
        )
        # A checked document last read by another version of Star Ground is
        # read again, so a reader change that alters its parts loses its mark.
        read_again = checked_by_hand and previous.reader != version()
        # A listed build without current board positions is fetched in full
        # too, so they can be found.
        needs_boards = previous.entry is not None and previous.boards < BOARDS_VERSION
        settled = previous.outcome in _SETTLED
        fetched = self._fetch(
            listing,
            previous,
            full=newly_checked or read_again or needs_boards or not settled,
        )
        if fetched is None:
            return
        if fetched.status == 304:
            # Only a settled document is asked for conditionally, so the
            # outcome it keeps is the one reported.
            kept = self._still(previous)
            self._refresh(previous, listing, kept)
            self._record(
                url,
                replace(previous, verification=kept),
                _unchanged(self.publisher, url, previous, "304 Not Modified", kept),
            )
            return
        digest = document_hash(fetched.content)
        fetched_state = replace(
            previous,
            sha256=digest,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
        )
        if digest == previous.sha256 and settled and not (newly_checked or read_again):
            self._same_document(
                listing, previous, fetched_state, fetched.content, needs_boards
            )
            return
        self._read_and_list(
            listing, previous, fetched_state, fetched.content, digest, checked
        )

    def _fetch(
        self, listing: Listing, previous: DocumentState, *, full: bool
    ) -> Fetched | None:
        """Fetches a document, or records why it couldn't be.

        Args:
            listing: The document as its page links it.
            previous: What the last run saw of it.
            full: Fetch it whole, not only if it changed since last time.

        Returns:
            The response, or None when the outcome is already recorded.
        """
        url, publisher = listing.document, self.publisher
        try:
            allows = self.source.allows
            if self.source.visit_page_first:
                self.client.get(listing.page, allows=allows)
            if full:
                return self.client.get(url, allows=allows)
            return self.client.get(
                url, previous.etag, previous.last_modified, allows=allows
            )
        except BlockedError as error:
            self._record(
                url,
                replace(previous, outcome=Outcome.BLOCKED, reason=str(error)),
                Event(publisher, url, Outcome.BLOCKED, str(error)),
            )
        except GoneError as error:
            # A dead link hides the build.
            self._unlist(previous)
            self._record(
                url,
                replace(previous, outcome=Outcome.GONE, reason=str(error), entry=None),
                Event(publisher, url, Outcome.GONE, str(error)),
            )
        except DisallowedError as error:
            self._record(
                url,
                replace(previous, outcome=Outcome.DISALLOWED, reason=str(error)),
                Event(publisher, url, Outcome.DISALLOWED, str(error)),
            )
        except Exception as error:  # A failed fetch is reported; the rest go on.
            reason = f"couldn't fetch it: {error}"
            # A file broken on the publisher's side fails every night; it
            # fails the job the first time, then is only listed.
            again = previous.outcome == Outcome.FAILED
            self._record(
                url,
                replace(previous, outcome=Outcome.FAILED, reason=reason),
                Event(
                    publisher,
                    url,
                    Outcome.FAILED,
                    f"{reason} (it failed last run too)" if again else reason,
                    again=again,
                ),
            )
        return None

    def _same_document(
        self,
        listing: Listing,
        previous: DocumentState,
        fetched_state: DocumentState,
        content: bytes,
        needs_boards: bool,
    ) -> None:
        """Records a document whose bytes haven't changed, without reading it."""
        url = listing.document
        kept = self._still(previous)
        self._refresh(previous, listing, kept)
        how = "same hash as last time"
        added = previous.boards
        if needs_boards and previous.entry:
            path = self._entry_path(previous.entry)
            listed = CatalogEntry.load(path)
            listed.with_boards(self._boards(listed, content)).write(path)
            how += "; board positions added"
            added = BOARDS_VERSION
        self._record(
            url,
            replace(
                fetched_state,
                outcome=previous.outcome,
                verification=kept,
                boards=added,
            ),
            _unchanged(self.publisher, url, previous, how, kept),
        )

    def _read_and_list(
        self,
        listing: Listing,
        previous: DocumentState,
        fetched_state: DocumentState,
        content: bytes,
        digest: str,
        checked: str,
    ) -> None:
        """Reads a document and lists it, if it reads cleanly.

        It's new or changed, or its last reading needs doing again (it failed,
        it's gone and back, or it was checked by hand and the reader changed).
        """
        url, publisher = listing.document, self.publisher
        try:
            reading = self.read(FileUpload(listing.name, content))
            if reading is None:
                raise ValueError("not a document Star Ground reads")
            accepted = catalog_reading(reading)
            why = clean(accepted)
            parts = fingerprint(accepted)
        except Exception as error:  # A reading bug is reported; the rest go on.
            reason = f"couldn't read it: {type(error).__name__}: {error}"
            # The last good reading stays listed, and the state keeps what it
            # was read from, so the next run fetches the document in full and
            # tries again. A reading that fails every night fails the job the
            # first time, then is only listed.
            again = previous.outcome == Outcome.FAILED
            self._record(
                url,
                replace(previous, outcome=Outcome.FAILED, reason=reason),
                Event(
                    publisher,
                    url,
                    Outcome.FAILED,
                    f"{reason} (it failed last run too)" if again else reason,
                    again=again,
                ),
            )
            return
        verification = self.verified.check(digest, parts)
        fetched_state = replace(
            fetched_state,
            parts=parts,
            verification=verification,
            title=accepted.title,
            reader=version(),
        )
        if verification == Verification.CHANGED:
            self.reporter.warning(
                f"{url} was checked by hand, but today's reading of the same file "
                "differs from the one checked: likely a parser regression",
                title=f"{publisher}: verified reading changed",
            )
        is_verified = verification == Verification.VERIFIED
        # A hand check can't make a build of no parts worth listing.
        empty = any(not build.inventory for _, build in builds(accepted))
        if why is not None and is_verified and not empty:
            # Checked by hand, so its warnings were looked into.
            self.reporter.info(
                f"listed though not clean, as it was checked by hand ({why})"
            )
        elif why is not None:
            self._unlist(previous)
            self._record(
                url,
                replace(
                    fetched_state, outcome=Outcome.NOT_CLEAN, reason=why, entry=None
                ),
                Event(publisher, url, Outcome.NOT_CLEAN, why),
            )
            return
        listed = self._entry(listing, digest, accepted, checked, is_verified)
        listed = listed.with_boards(self._boards(listed, content))
        listed.write(self._entry_path(listed.id))
        outcome, reason = _relisted(previous, digest, parts)
        self._record(
            url,
            replace(
                fetched_state,
                outcome=outcome,
                reason=reason,
                entry=listed.id,
                boards=BOARDS_VERSION,
            ),
            Event(publisher, url, outcome, reason, verified=is_verified),
        )

    def _entry(
        self,
        listing: Listing,
        digest: str,
        reading: ParseResult,
        checked: str,
        verified: bool,
    ) -> CatalogEntry:
        """A listed document's entry: only what was derived from it."""
        entry_key = entry_id(listing)
        taken = self._entry_path(entry_key)
        if taken.exists() and CatalogEntry.load(taken).document != listing.document:
            # Two documents published under one file name.
            entry_key += "-" + sha256(listing.document.encode()).hexdigest()[:6]
        title = entry_title(reading.title, listing)
        return CatalogEntry(
            id=entry_key,
            publisher=self.source.publisher,
            title=title,
            effects=self._effects(entry_key, title, listing),
            document=listing.document,
            page=listing.page,
            file_name=listing.name,
            sha256=digest,
            checked=checked,
            read_by_ocr=reading.ocr is not None and reading.ocr.has_parts,
            verified=verified,
            builds=CatalogEntry.builds_of(reading),
        )

    def _effects(
        self, entry_key: str, title: str, listing: Listing
    ) -> tuple[Effect, ...]:
        return tuple(
            effects(
                f"{self.publisher}/{entry_key}",
                title,
                listing.categories,
                self.overrides,
            )
        )

    def _entry_path(self, entry_key: str) -> Path:
        return self.builds_dir / f"{entry_key}.json"

    def _boards(
        self, listed: CatalogEntry, content: bytes
    ) -> dict[str, list[Location]]:
        """Where the document draws each of its entry's parts; none if that fails."""
        parts = listed.parts
        # As the entry keeps them, which is what the finder was always given.
        listing = [rounded(p.location) for p in parts if p.location]
        try:
            refs = [p.ref for p in parts if p.ref]
            return self.locate_boards(content, refs, listing)
        except Exception as error:  # The build is still listed, without them.
            self.reporter.warning(
                f"couldn't find where {listed.id} draws its parts: "
                f"{type(error).__name__}: {error}",
                title=f"{self.publisher}: board positions",
            )
            return {}

    def _still(self, previous: DocumentState) -> Verification:
        """A reading kept from an earlier run, checked against today's list."""
        if previous.sha256 is None or previous.parts is None:
            return Verification.UNVERIFIED
        return self.verified.check(previous.sha256, previous.parts)

    def _refresh(
        self, previous: DocumentState, listing: Listing, verification: Verification
    ) -> None:
        """Brings an unread build's entry up to date with its page and the lists.

        Its mark follows the verified list, and its title and effects follow
        its page and `effects.json`, which may have changed since it was read.
        """
        if not previous.entry:
            return
        path = self._entry_path(previous.entry)
        if not path.exists():
            return
        listed = CatalogEntry.load(path)
        title = entry_title(previous.title, listing)
        updated = replace(
            listed,
            title=title,
            effects=self._effects(previous.entry, title, listing),
            verified=verification == Verification.VERIFIED,
        )
        if updated != listed:
            updated.write(path)

    def _unlist(self, previous: DocumentState) -> None:
        """Removes a document's entry, if it has one."""
        if previous.entry:
            self._entry_path(previous.entry).unlink(missing_ok=True)

    def _record(self, url: str, document: DocumentState, event: Event) -> None:
        self.state.documents[url] = replace(document, postponed=False)
        self.state.save(self.state_path)
        self.reporter.outcome(event)

    def _report_discovery(self, found: Discovery) -> None:
        for page, reason in sorted(found.failures.items()):
            self.reporter.page_failed(self.publisher, page, reason)
        self.reporter.info(
            f"{len(found.listings)} build documents linked from {len(found.pages)} "
            f"pages ({len(found.failures)} pages not read)"
        )
        whole = self.sample.pages is None and found.complete
        self.state.pages = found.pages if whole else {**self.state.pages, **found.pages}

    def _remove_unlinked(self, found: Discovery) -> None:
        """Forgets the documents no page links any more, and their entries."""
        gone = [url for url in self.state.documents if url not in found.listings]
        for url in gone:
            self._unlist(self.state.documents.pop(url))
            self.reporter.outcome(
                Event(
                    self.publisher,
                    url,
                    Outcome.REMOVED,
                    "no longer linked from the site",
                )
            )
        if not gone:
            self.reporter.info("none")
