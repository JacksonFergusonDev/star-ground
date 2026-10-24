"""Downloads the test corpus's build documents from their publishers.

The repository keeps no publisher documents. `tests/samples/manifest.json`
lists each one's publisher URL and SHA-256, and this tool fetches them into
`tests/samples/` (ignored by Git), verifying each against its hash. Files
already present with the right hash are left alone, so it's cheap to rerun.

A hash mismatch means the publisher revised the document: check the new
version against its golden parts list, then update the manifest.

Run it with `just fetch-samples`.
"""

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "tests" / "samples"
MANIFEST = SAMPLES_DIR / "manifest.json"
USER_AGENT = "star-ground-tests (+https://github.com/JacksonFergusonDev/star-ground)"
# Pause between downloads, to go easy on the publisher's server.
POLITE_DELAY = 1.0


@dataclass(frozen=True, slots=True)
class Sample:
    """One build document in the test corpus.

    Attributes:
        path: Where it lives, relative to `tests/samples/`.
        url: The publisher's URL for it.
        sha256: The hash of the version the golden parts list was made from.
    """

    path: str
    url: str
    sha256: str


def load_manifest(manifest: Path = MANIFEST) -> list[Sample]:
    """Reads the corpus manifest.

    Args:
        manifest: The manifest file.

    Returns:
        The corpus's documents.
    """
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    return [Sample(d["path"], d["url"], d["sha256"]) for d in raw["documents"]]


def missing(samples: list[Sample], root: Path = SAMPLES_DIR) -> list[Sample]:
    """The documents not yet downloaded.

    Args:
        samples: The corpus's documents.
        root: The directory they're downloaded into.

    Returns:
        Each document with no file at its path.
    """
    return [s for s in samples if not (root / s.path).is_file()]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(samples: list[Sample], root: Path = SAMPLES_DIR) -> list[str]:
    """Downloads every document that's absent or doesn't match its hash.

    Args:
        samples: The corpus's documents.
        root: The directory to download into.

    Returns:
        One message per document that couldn't be fetched or verified.
    """
    errors = []
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for sample in samples:
        dest = root / sample.path
        if dest.is_file() and _digest(dest.read_bytes()) == sample.sha256:
            continue
        try:
            response = session.get(sample.url, timeout=60)
            response.raise_for_status()
        except requests.RequestException as e:
            errors.append(f"{sample.path}: download failed: {e}")
            continue
        if _digest(response.content) != sample.sha256:
            errors.append(
                f"{sample.path}: the publisher's document no longer matches its hash."
                " Check the new version against its golden parts list, then"
                " update tests/samples/manifest.json."
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        print(f"Fetched {sample.path}")
        time.sleep(POLITE_DELAY)
    return errors


def main() -> int:
    """Fetches the corpus and reports any failures.

    Returns:
        The exit code: 1 if any document couldn't be fetched or verified.
    """
    errors = fetch(load_manifest())
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
