"""A listed build's catalog entry, and how it is written to diff cleanly.

Each entry is `builds/<publisher>/<id>.json`: only what was derived from the
document (its parts, where its board draws them, what kind of effect it is),
never the document itself. The website reads these files as they are.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from star_ground.api import counted_parts, read_parts
from star_ground.bom_lib import ParseResult
from star_ground.bom_lib.types import Location
from star_ground.catalog.verified import builds


@dataclass(frozen=True, slots=True)
class EntryPart:
    """One part of a listed build.

    Attributes:
        ref: Its designator; None for a part counted by value ('2 - 47', as
            Tonepad lists most of a build).
        count: How many: 1 for a designator, the count for a counted part.
        category: Its category, as `ComponentCategory` names it.
        value: Its value.
        location: Where the document lists it, when it was found.
    """

    ref: str | None
    count: int
    category: str
    value: str
    location: Location | None = None


@dataclass(frozen=True, slots=True)
class EntryBuild:
    """One build of a listed document, with its parts.

    Attributes:
        name: The build's name; None for a single-build document.
        parts: Each designator, then each part counted by value.
    """

    name: str | None
    parts: tuple[EntryPart, ...]

    @classmethod
    def read(cls, name: str | None, reading: ParseResult) -> EntryBuild:
        """A build's parts as the reading found them.

        Args:
            name: The build's name, or None.
            reading: The build's reading.

        Returns:
            The build.
        """
        designated = [
            EntryPart(p.ref, 1, p.category.value, p.value, p.location)
            for p in read_parts(reading)
        ]
        counted = [
            EntryPart(None, p.count, p.category.value, p.value)
            for p in counted_parts(reading)
        ]
        return cls(name, (*designated, *counted))


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A listed document's catalog entry.

    Attributes:
        id: Its id (`build.entry_id`, made unique among the publisher's).
        publisher: The publisher's id.
        title: The build's title (`build.entry_title`).
        effects: What kind of effect it is (`effects.effects`).
        document: The document's URL.
        page: The page that links it.
        file_name: The document's published file name.
        sha256: The document's `verified.document_hash`.
        checked: When it was fetched, as an ISO timestamp.
        read_by_ocr: Whether its parts list was read by OCR.
        verified: Whether its reading was checked by hand.
        builds: Each build, with its parts.
        boards: Where the document draws each designator outside its parts
            list, by designator.
    """

    id: str
    publisher: str
    title: str
    effects: tuple[str, ...]
    document: str
    page: str
    file_name: str
    sha256: str
    checked: str
    read_by_ocr: bool
    verified: bool
    builds: tuple[EntryBuild, ...]
    boards: Mapping[str, tuple[Location, ...]] = field(default_factory=dict)

    @staticmethod
    def builds_of(reading: ParseResult) -> tuple[EntryBuild, ...]:
        """Each build of a reading, as an entry lists it."""
        return tuple(EntryBuild.read(name, build) for name, build in builds(reading))

    @property
    def parts(self) -> list[EntryPart]:
        """Every part of every build."""
        return [part for build in self.builds for part in build.parts]

    def with_boards(self, boards: Mapping[str, list[Location]]) -> CatalogEntry:
        """The entry with where its board draws each part."""
        return replace(
            self, boards={ref: tuple(places) for ref, places in boards.items()}
        )

    @classmethod
    def load(cls, path: Path) -> CatalogEntry:
        """Reads an entry written by `write`.

        Args:
            path: The entry's file.

        Returns:
            The entry.
        """
        raw = json.loads(path.read_text())
        return cls(
            **{
                **raw,
                "effects": tuple(raw["effects"]),
                "builds": tuple(
                    EntryBuild(
                        build["name"],
                        tuple(
                            EntryPart(
                                **{
                                    **part,
                                    "location": _location(part["location"]),
                                }
                            )
                            for part in build["parts"]
                        ),
                    )
                    for build in raw["builds"]
                ),
                # Entries listed before board positions were found have none.
                "boards": {
                    ref: tuple(Location(**place) for place in places)
                    for ref, places in raw.get("boards", {}).items()
                },
            }
        )

    def write(self, path: Path) -> None:
        """Writes the entry with one part, and one designator's positions, per line.

        So a change to one part diffs as one line, rather than a field per line.

        Args:
            path: The entry's file.
        """
        data = asdict(self)
        members = [
            f" {json.dumps(key)}: "
            f"{to_json(_rounded(value), _ENTRY_LAYOUT.get(key, 0), ' ')}"
            for key, value in data.items()
        ]
        write_atomically(path, "{\n" + ",\n".join(members) + "\n}\n")


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


def rounded(location: Location) -> Location:
    """A location as an entry keeps it: to a hundredth of a point."""
    return Location(**_rounded(asdict(location)))


def _location(raw: dict[str, Any] | None) -> Location | None:
    return Location(**raw) if raw else None


# How far each part of an entry is laid out a member per line: a build down
# to its parts, the boards down to each designator's positions.
_ENTRY_LAYOUT = {"builds": 3, "boards": 1}


def to_json(value: Any, expand: int, indent: str = "") -> str:
    """`value` as JSON, its outer `expand` levels laid out a member per line.

    Args:
        value: Plain JSON data.
        expand: How many levels to lay out a member per line.
        indent: The indent of the level `value` sits at.

    Returns:
        The JSON text.
    """
    if expand == 0 or not isinstance(value, dict | list | tuple) or not value:
        return json.dumps(value)
    inner = indent + " "
    if isinstance(value, dict):
        members = [
            f"{inner}{json.dumps(key)}: {to_json(member, expand - 1, inner)}"
            for key, member in value.items()
        ]
        return "{\n" + ",\n".join(members) + f"\n{indent}}}"
    items = [f"{inner}{to_json(item, expand - 1, inner)}" for item in value]
    return "[\n" + ",\n".join(items) + f"\n{indent}]"


def _rounded(value: Any) -> Any:
    """`value` with its coordinates kept to a hundredth of a point."""
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {key: _rounded(member) for key, member in value.items()}
    if isinstance(value, list | tuple):
        return [_rounded(item) for item in value]
    return value
