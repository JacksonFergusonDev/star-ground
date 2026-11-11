"""How the catalog's files are written: whole, or not at all."""

import os
import tempfile
from pathlib import Path


def write_atomically(path: Path, text: str) -> None:
    """Writes a file whole or not at all, so a job killed mid-write leaves the old one.

    The text goes to a temporary file beside it, which then replaces it.

    Args:
        path: The file; its directory is made if needed.
        text: What it should hold.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    written = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary:
            temporary.write(text)
        # As `write_text` leaves a new file, not `mkstemp`'s owner-only.
        written.chmod(0o644)
        os.replace(written, path)
    except BaseException:
        written.unlink(missing_ok=True)
        raise
