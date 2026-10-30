"""Which catalog documents have been checked by hand, and what was checked.

A build is **verified** only when someone has checked that exact file's parts
against the document: its hash is on the list (`catalog/verified.json`), and
today's reading of it matches the reading that was checked (`fingerprint`).
Everything else is **unverified**: read automatically, not yet checked.

Keeping the reading's fingerprint, not just the file's hash, means a parser
change that alters a checked document's parts can't keep its mark: the build
goes back to unverified and the run flags it (`Verification.CHANGED`).

The list is grown with `tools/verify_documents.py`, which grades the job's
own reading of a labelled document and adds it only when every part is right.
"""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from src.api import read_parts
from src.bom_lib import ParseResult


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

    Locations, warnings and the title don't count: they don't change what's
    bought. Order doesn't count either.
    """
    canonical = sorted(
        (
            name or "",
            sorted((p.ref, p.category.value, p.value) for p in read_parts(build)),
        )
        for name, build in builds(result)
    )
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
        """Writes the list, sorted so additions diff cleanly."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            digest: {"parts": c.parts, "checked": c.checked}
            for digest, c in sorted(self.checked.items())
        }
        path.write_text(json.dumps(data, indent=1) + "\n")

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
