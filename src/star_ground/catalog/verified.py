"""Which catalog documents have been checked by hand, and what was checked.

A build is **verified** only when someone has checked that exact file's parts
against the document: its hash (`document_hash`) is on the list
(`catalog/verified.json`), and
today's reading of it matches the reading that was checked (`fingerprint`).
Everything else is **unverified**: read automatically, not yet checked.

Keeping the reading's fingerprint, not just the file's hash, means a parser
change that alters a checked document's parts can't keep its mark: the build
goes back to unverified and the run flags it (`Verification.CHANGED`). The
job reads a checked document again whenever Star Ground's version differs
from the one that last read it, even when the file hasn't changed, so the
check happens on the first night after a release.

The list is grown with `scripts/verify_documents.py`, which grades the job's
own reading of a labelled document and adds it only when every part is right.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from star_ground.api import counted_parts, read_parts
from star_ground.bom_lib import ParseResult
from star_ground.catalog.files import write_atomically


def document_hash(content: bytes) -> str:
    """A PDF's SHA-256, up to and including its final `%%EOF` marker.

    A PDF ends at `%%EOF`, and readers ignore what follows. Tonepad stamps
    each download there with the downloader's address and the time, so the
    whole file's hash differs on every download; this one doesn't. A file
    with no marker is hashed whole.

    Args:
        content: The file's bytes.

    Returns:
        The hash, in hex.
    """
    end = content.rfind(b"%%EOF")
    return hashlib.sha256(content[: end + 5] if end != -1 else content).hexdigest()


class Verification(StrEnum):
    """Whether a document's reading has been checked by hand."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class Checked:
    """A document checked by hand.

    Attributes:
        parts: The fingerprint of the reading that was checked.
        checked: When (an ISO 8601 date).
    """

    parts: str
    checked: str


def builds(result: ParseResult) -> list[tuple[str | None, ParseResult]]:
    """A reading's builds: each named one, or the reading itself."""
    return [(v.name, result.select(v.name)) for v in result.variants] or [
        (None, result)
    ]


def fingerprint(result: ParseResult) -> str:
    """A hash of everything a builder buys from a reading: each build's parts.

    That's the parts read by designator and the parts counted by value with
    none (most of a Tonepad build). Locations, warnings and the title don't
    count: they don't change what's bought. Order doesn't count either.
    """
    canonical: list[tuple[object, ...]] = []
    # By build name, which is unique within a reading.
    for name, build in sorted(builds(result), key=lambda b: b[0] or ""):
        read = sorted((p.ref, p.category.value, p.value) for p in read_parts(build))
        counted = sorted(
            (p.category.value, p.value, p.count) for p in counted_parts(build)
        )
        # A build that counts nothing leaves the count out, so its
        # fingerprint is the one checked before counted parts were included.
        canonical.append((name or "", read, counted) if counted else (name or "", read))
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


class VerifiedList:
    """The documents checked by hand, by their SHA-256.

    Args:
        checked: Each checked document, by hash.
    """

    def __init__(self, checked: dict[str, Checked] | None = None) -> None:
        self.checked = checked or {}

    @classmethod
    def load(cls, path: Path) -> VerifiedList:
        """Reads the list, or an empty one when there's none yet."""
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls({digest: Checked(**entry) for digest, entry in raw.items()})

    def save(self, path: Path) -> None:
        """Writes the list, sorted so additions diff cleanly.

        It's written whole or not at all, so an interrupted save leaves the
        last list.
        """
        data = {
            digest: {"parts": c.parts, "checked": c.checked}
            for digest, c in sorted(self.checked.items())
        }
        write_atomically(path, json.dumps(data, indent=1) + "\n")

    def check(self, digest: str, parts: str) -> Verification:
        """Whether a document's reading is the one checked by hand.

        Args:
            digest: The file's SHA-256.
            parts: The `fingerprint` of today's reading of it.

        Returns:
            Verified when both the file and its reading match; changed when
            the file was checked but now reads differently; else unverified.
        """
        known = self.checked.get(digest)
        if known is None:
            return Verification.UNVERIFIED
        if known.parts != parts:
            return Verification.CHANGED
        return Verification.VERIFIED
