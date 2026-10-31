"""Where each part is drawn on a document's board and schematic.

Step 7 of `PLAN.md` lights a part up on the board render or schematic when a
builder points at its row. Finding those labels can take OCR, which costs as
much as reading a parts list (measured by `tools/measure_board_ocr.py`), so
it runs only in the catalog job (`src/catalog/`), never on the API.

A designator is found in two ways:

- **Text:** a word in the text layer (Aion FX's and Tonepad's vector boards
  and schematics print about half their labels as text).
- **Images:** the largest embedded images, read by OCR upright and turned 90
  degrees. RapidOCR turns only crops taller than 1.5:1, so a short sideways
  label like 'R1' is read only on the turned pass.

Labels inside the parts list itself (the places `locations.locate` found) are
left out: those rows are already outlined.
"""

import hashlib
import io
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pdfplumber

from src.bom_lib.designators import is_designator
from src.bom_lib.ocr import OcrBox, Recognizer, recognize
from src.bom_lib.types import Location

# The parts-list OCR's resolution: it reads 'R27' reliably at this size.
_DPI = 200
# An image smaller than this share of its page is a logo or an icon.
_MIN_SHARE = 0.08
# Two marks overlapping this much of the smaller are one label read twice.
_SAME_MARK = 0.5
# Words within this many points of each other's top share a line.
_LINE_PT = 2
# A line with more words than this is prose, not a drawing's labels.
_LABEL_WORDS = 6
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_REPEAT = re.compile(r" \(\d+\)$")


@dataclass(frozen=True, slots=True)
class BoardMark:
    """A place a designator is drawn, outside the parts list.

    Attributes:
        ref: The designator, as the parts list names it ('R1').
        location: Its box on the page.
    """

    ref: str
    location: Location


def base_ref(ref: str) -> str:
    """The designator a label names: 'R1' for 'R1 (2)', a second board's R1."""
    return _REPEAT.sub("", ref)


def _tokens(text: str) -> Iterable[tuple[str, int, int]]:
    """Each word of a run of text, with where it starts and ends."""
    for match in _TOKEN.finditer(text):
        yield match.group().upper(), match.start(), match.end()


def _overlap(a: Location, b: Location) -> float:
    """How much of the smaller box the two share."""
    if a.page != b.page:
        return 0.0
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.bottom, b.bottom) - max(a.top, b.top)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min(
        (a.x1 - a.x0) * (a.bottom - a.top), (b.x1 - b.x0) * (b.bottom - b.top)
    )
    return width * height / smaller if smaller > 0 else 0.0


def _text_marks(
    page: Any, number: int, wanted: Mapping[str, str]
) -> Iterable[BoardMark]:
    """Designators printed as words, on lines short enough to be labels.

    A board or schematic prints its labels apart; a line of prose ('R1 sets
    the gain') is a note about the part, not where it's drawn.
    """
    words = page.extract_words()
    lines = Counter(round(w["top"] / _LINE_PT) for w in words)
    for word in words:
        if lines[round(word["top"] / _LINE_PT)] > _LABEL_WORDS:
            continue
        text = word["text"].strip(",;:()[]").upper()
        # A named control ('Gain') printed as a word is usually prose: the
        # controls list, the notes. Only its drawn label counts.
        if text in wanted and is_designator(text):
            yield BoardMark(
                wanted[text],
                Location(number, word["x0"], word["top"], word["x1"], word["bottom"]),
            )


def _box_marks(
    box: OcrBox, wanted: Mapping[str, str], page: int, turned: bool
) -> Iterable[BoardMark]:
    """The designators an OCR box names.

    A box can hold several labels ('R1 10k'). Upright, each gets the stretch
    of the box its characters take. Turned, each gets the whole box: OCR may
    have read it flipped, so which end the text starts at isn't known.
    """
    length = max(len(box.text), 1)
    for token, start, end in _tokens(box.text):
        if token not in wanted:
            continue
        if turned:
            location = Location(page, box.x0, box.top, box.x1, box.bottom)
        else:
            span = box.x1 - box.x0
            location = Location(
                page,
                box.x0 + start / length * span,
                box.top,
                box.x0 + end / length * span,
                box.bottom,
            )
        yield BoardMark(wanted[token], location)


def _turned_back(box: OcrBox, width: float) -> OcrBox:
    """A box read on an image turned 90 degrees counter-clockwise, as upright.

    Turning maps an upright point (x, y) to (y, width - x), so a turned box
    (u, v) came from x = width - v, y = u.
    """
    return OcrBox(box.text, width - box.bottom, width - box.top, box.x0, box.x1)


def _image_key(image: Mapping[str, Any]) -> str:
    stream = image.get("stream")
    data = stream.get_rawdata() if stream is not None else b""
    return hashlib.sha256(data or repr(image["srcsize"]).encode()).hexdigest()


def _images(doc: Any) -> list[tuple[int, Any, tuple[float, float, float, float]]]:
    """Each distinct image big enough to be a drawing: page number, page, box.

    Every one is read. Size doesn't pick out the board: Kliche Overdrive's
    wiring diagrams are bigger than its board render. Reading the drilling
    guides and photos too costs only the offline job's time.
    """
    found: dict[str, tuple[int, Any, tuple[float, float, float, float]]] = {}
    for number, page in enumerate(doc.pages):
        area = float(page.width * page.height)
        for image in page.images:
            box = (
                max(float(image["x0"]), 0.0),
                max(float(image["top"]), 0.0),
                min(float(image["x1"]), float(page.width)),
                min(float(image["bottom"]), float(page.height)),
            )
            size = (box[2] - box[0]) * (box[3] - box[1])
            key = _image_key(image)
            if size / area >= _MIN_SHARE and key not in found:
                found[key] = (number, page, box)
    return list(found.values())


def _image_marks(
    page: Any,
    number: int,
    box: tuple[float, float, float, float],
    wanted: Mapping[str, str],
    recognizer: Recognizer,
) -> Iterable[BoardMark]:
    x0, top, x1, _ = box
    image = page.crop(box).to_image(resolution=_DPI).original.convert("RGB")
    scale = 72 / _DPI

    def placed(b: OcrBox) -> OcrBox:
        return OcrBox(b.text, b.x0 + x0, b.x1 + x0, b.top + top, b.bottom + top)

    for found in recognizer(image, scale):
        yield from _box_marks(placed(found), wanted, number, turned=False)
    width = x1 - x0
    for found in recognizer(image.rotate(90, expand=True), scale):
        yield from _box_marks(
            placed(_turned_back(found, width)), wanted, number, turned=True
        )


def _distinct(
    marks: Iterable[BoardMark], listing: Collection[Location]
) -> list[BoardMark]:
    """The marks outside the parts list, each label once."""
    kept: list[BoardMark] = []
    for mark in marks:
        if any(_overlap(mark.location, row) > 0 for row in listing):
            continue
        if any(
            k.ref == mark.ref and _overlap(k.location, mark.location) > _SAME_MARK
            for k in kept
        ):
            continue
        kept.append(mark)
    return kept


def board_marks(
    content: bytes,
    refs: Iterable[str],
    listing: Collection[Location] = (),
    recognizer: Recognizer = recognize,
) -> list[BoardMark]:
    """Finds where a document draws each designator on its board and schematic.

    Args:
        content: The PDF's bytes.
        refs: The designators its parts list names ('R1', 'R1 (2)', 'VOLUME').
        listing: Where the parts list prints them (`ParseResult.locations`):
            labels there are left out.
        recognizer: The OCR engine (tests substitute a fake).

    Returns:
        Each place a designator is drawn, in page order; a designator can be
        drawn several times (board and schematic).
    """
    wanted = {base_ref(ref).upper(): base_ref(ref) for ref in refs}
    marks: list[BoardMark] = []
    with pdfplumber.open(io.BytesIO(content)) as doc:
        for number, page in enumerate(doc.pages):
            marks.extend(_text_marks(page, number, wanted))
        for number, page, box in _images(doc):
            marks.extend(_image_marks(page, number, box, wanted, recognizer))
    marks.sort(key=lambda m: (m.location.page, m.location.top, m.location.x0))
    return _distinct(marks, listing)


def marks_by_ref(marks: Sequence[BoardMark]) -> dict[str, list[Location]]:
    """Each designator's places, in page order."""
    grouped: dict[str, list[Location]] = {}
    for mark in marks:
        grouped.setdefault(mark.ref, []).append(mark.location)
    return grouped
