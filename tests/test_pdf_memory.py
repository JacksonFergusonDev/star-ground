"""A small PDF can't make the reader take gigabytes of memory.

Each case is a hostile file built here, read while `tracemalloc` records the
peak, compared with the bound the reader keeps (`star_ground.bom_lib.pdf.guard`)
rather than a number of megabytes: the same file would take about its size
decompressed, or its page count times a page's layout, without the bound.
"""

import gc
import tracemalloc
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pdfminer.pdftypes
import pytest
from pdfminer.pdftypes import decompress_corrupted

from star_ground.bom_lib import ocr
from star_ground.bom_lib.boards import board_marks
from star_ground.bom_lib.errors import (
    CrowdedPageError,
    HeavyPageError,
    HeavyStreamError,
)
from star_ground.bom_lib.ocr import OcrBox
from star_ground.bom_lib.pdf import guard
from star_ground.bom_lib.pdf.content import inflate
from star_ground.bom_lib.pdf.document import parse_pdf
from tests.pdfs import assemble, stream
from tests.test_hostile_input import _drawing, _form, _tiny_ones
from tests.test_hostile_input import _pdf as _one_page_pdf

MIB = 1 << 20


def _peak(read: Callable[[], Any]) -> tuple[int, BaseException | None]:
    """The most memory a call took, and what it raised, if anything."""
    tracemalloc.start()
    error = None
    try:
        read()
    except Exception as e:
        error = e
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return peak, error


def _compressed(extra: int, head: bytes = b"") -> bytes:
    """A Flate stream of `head` and then `extra` spaces: a bomb when large."""
    packer = zlib.compressobj(9)
    out = packer.compress(head)
    chunk = b" " * MIB
    for _ in range(extra // MIB):
        out += packer.compress(chunk)
    return out + packer.flush()


def _structured(*, xref_padding: int = 0, objects_padding: int = 0) -> bytes:
    """A blank-page PDF whose objects are in an object stream, found by an
    xref stream, either padded with spaces to decode to far more.

    Args:
        xref_padding: Bytes the cross-reference stream decodes to beyond its entries.
        objects_padding: Bytes the object stream decodes to beyond its objects.
    """
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    header, offset = b"", 0
    for number, body in enumerate(bodies, start=1):
        header += f"{number} {offset} ".encode()
        offset += len(body) + 1
    inner = header + b"\n".join(bodies)
    objstm = _compressed(objects_padding, inner)
    out = b"%PDF-1.5\n"
    objstm_at = len(out)
    out += (
        f"4 0 obj\n<< /Type /ObjStm /N 3 /First {len(header)} /Filter /FlateDecode "
        f"/Length {len(objstm)} >>\nstream\n".encode()
        + objstm
        + b"\nendstream\nendobj\n"
    )
    xref_at = len(out)

    def entry(kind: int, a: int, b: int) -> bytes:
        return bytes([kind]) + a.to_bytes(3, "big") + bytes([b])

    entries = (
        entry(0, 0, 255)
        + b"".join(entry(2, 4, i) for i in range(3))
        + entry(1, objstm_at, 0)
        + entry(1, xref_at, 0)
    )
    packed = _compressed(xref_padding, entries)
    out += (
        f"5 0 obj\n<< /Type /XRef /Size 6 /W [1 3 1] /Root 1 0 R "
        f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n".encode()
        + packed
        + b"\nendstream\nendobj\n"
    )
    return out + f"startxref\n{xref_at}\n%%EOF\n".encode()


def _read(path: Path) -> None:
    parse_pdf(str(path), "Hostile")


def test_structured_pdf_without_padding_is_read(tmp_path: Path) -> None:
    path = tmp_path / "plain.pdf"
    path.write_bytes(_structured())
    _read(path)


@pytest.mark.parametrize("which", ["xref_padding", "objects_padding"])
def test_compressed_structure_that_decodes_to_gigabytes_is_refused(
    tmp_path: Path, which: str
) -> None:
    # pdfminer decompressed the cross-reference stream and every object
    # stream whole as the file opened, before any page was checked.
    path = tmp_path / "bomb.pdf"
    path.write_bytes(_structured(**{which: 8 * guard.MAX_DOCUMENT_STREAM_BYTES}))
    assert path.stat().st_size < 1 << 20
    peak, error = _peak(lambda: _read(path))
    assert isinstance(error, HeavyStreamError)
    assert peak < 3 * guard.MAX_DOCUMENT_STREAM_BYTES


def _pages(count: int, chars: int) -> bytes:
    """A PDF of `count` pages, each drawing the same `chars` characters."""
    text = b"BT /F1 6 Tf 10 700 Td (" + b"a" * chars + b") Tj ET"
    kids = " ".join(f"{4 + n} 0 R" for n in range(count))
    return assemble(
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + f"{4 + count} 0 R >>".encode()
            for _ in range(count)
        ),
        stream(text),
    )


def test_pages_are_released_as_they_are_read(tmp_path: Path) -> None:
    # pdfplumber keeps each page's layout until the document closes: 60 pages
    # of 20,000 characters took 2 GB.
    one, many = tmp_path / "one.pdf", tmp_path / "many.pdf"
    one.write_bytes(_pages(1, 5_000))
    many.write_bytes(_pages(20, 5_000))
    _read(one)  # Warm up imports before measuring either file.
    gc.collect()
    gc_enabled = gc.isenabled()
    gc.disable()  # Page caches must be cleared even between collections.
    try:
        peak_one, error = _peak(lambda: _read(one))
        assert error is None
        peak_many, error = _peak(lambda: _read(many))
        assert error is None
    finally:
        if gc_enabled:
            gc.enable()
        gc.collect()
    assert peak_many < 2 * peak_one


def test_released_pages_are_read_as_before(tmp_path: Path) -> None:
    path = tmp_path / "list.pdf"
    path.write_bytes(_drawing(b""))
    assert {
        r for p in parse_pdf(str(path), "P").inventory.values() for r in p.refs
    } == {"R1"}


def _no_text(image: Any, scale: float) -> list[OcrBox]:
    return []


HOSTILE = {
    "bomb": (lambda: _drawing(b" " * (16 * MIB)), HeavyPageError),
    "crowded": (
        lambda: _one_page_pdf(_tiny_ones(guard.MAX_PAGE_CHARS + 1)),
        CrowdedPageError,
    ),
    "structure": (
        lambda: _structured(objects_padding=8 * guard.MAX_DOCUMENT_STREAM_BYTES),
        HeavyStreamError,
    ),
}


@pytest.mark.parametrize("kind", HOSTILE)
def test_board_marks_read_a_pdf_within_the_same_bounds(kind: str) -> None:
    make, expected = HOSTILE[kind]
    content = make()
    peak, error = _peak(lambda: board_marks(content, ["R1"], recognizer=_no_text))
    assert isinstance(error, expected)
    assert peak < 3 * guard.MAX_DOCUMENT_STREAM_BYTES


@pytest.mark.parametrize("kind", HOSTILE)
def test_ocr_reads_a_pdf_within_the_same_bounds(tmp_path: Path, kind: str) -> None:
    make, expected = HOSTILE[kind]
    path = tmp_path / "hostile.pdf"
    path.write_bytes(make())
    peak, error = _peak(lambda: ocr._read_images(str(path), "X", _no_text))
    assert isinstance(error, expected)
    assert peak < 3 * guard.MAX_DOCUMENT_STREAM_BYTES


def test_form_drawn_many_times_counts_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The page's own bytes and the form's, each once, are well within the
    # bound; the form's characters are kept once for every time it's drawn.
    monkeypatch.setattr(guard, "MAX_PAGE_CONTENT_BYTES", 64 << 10)
    form = _form(b"BT /F1 6 Tf 10 10 Td (abcdefghijklmnopqrstuvwxyz) Tj ET")
    path = tmp_path / "drawn.pdf"
    path.write_bytes(_drawing(b"/Fm0 Do " * 2_000, form))
    assert path.stat().st_size < 64 << 10
    peak, error = _peak(lambda: _read(path))
    assert isinstance(error, HeavyPageError)
    assert peak < 20 * MIB


def test_forms_drawn_within_forms_multiply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "MAX_PAGE_CONTENT_BYTES", 64 << 10)
    # Five forms, each drawing the next 20 times: 160,000 draws of the last.
    forms = range(5)
    names = " ".join(f"/Fm{n} {6 + n} 0 R" for n in forms)
    path = tmp_path / "nested.pdf"
    path.write_bytes(
        assemble(
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> /XObject << "
            + names.encode()
            + b" >> >> >>",
            stream(b"/Fm0 Do"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            *[
                stream(
                    (f"/Fm{n + 1} Do " * 20).encode()
                    if n < 4
                    else b"BT /F1 6 Tf 10 10 Td (abcdefghijklmnopqrstuvwxyz) Tj ET",
                    "/Subtype /Form /BBox [0 0 612 792] /Resources << /Font << "
                    "/F1 5 0 R >> /XObject << " + names + " >> >> ",
                )
                for n in forms
            ],
        )
    )
    peak, error = _peak(lambda: _read(path))
    assert isinstance(error, HeavyPageError)
    assert peak < 20 * MIB


def test_form_that_draws_itself_is_refused(tmp_path: Path) -> None:
    again = stream(
        b"/Fm0 Do",
        "/Subtype /Form /BBox [0 0 612 792] /Resources << /XObject << /Fm0 6 0 R >> >> ",
    )
    path = tmp_path / "again.pdf"
    path.write_bytes(_drawing(b"/Fm0 Do", again))
    with pytest.raises(HeavyPageError):
        _read(path)


def _as_pdfminer_decodes(data: bytes) -> bytes:
    """What `PDFStream.decode` makes of Flate data."""
    try:
        return zlib.decompress(data)
    except zlib.error:
        try:
            return decompress_corrupted(data)
        except zlib.error:
            return b""


def test_damaged_flate_data_is_read_as_pdfminer_reads_it() -> None:
    whole = zlib.compress(bytes(range(256)) * 400, 9)
    middle = bytearray(whole)
    middle[len(middle) // 2] ^= 0xFF
    cases = {
        "whole": whole,
        "junk after": whole + b"\x00junk",
        "bad checksum": whole[:-1] + bytes([whole[-1] ^ 0xFF]),
        "truncated": whole[: len(whole) // 2],
        "damaged in the middle": bytes(middle),
        "not flate": b"plain text, not compressed",
        "empty": b"",
    }
    for name, data in cases.items():
        got = inflate(data, 10 * MIB)
        assert got is not None, name
        assert got[0] == _as_pdfminer_decodes(data), name
    assert inflate(whole, 100) is None


def test_damaged_stream_is_not_read_a_byte_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pdfminer's fallback feeds the decompressor one byte at a time and joins
    # the output by copying it all: quadratic in the stream's size.
    def never(data: bytes) -> bytes:
        raise AssertionError("read a byte at a time")

    monkeypatch.setattr(pdfminer.pdftypes, "decompress_corrupted", never)
    listing = zlib.compress(b"BT /F1 10 Tf 50 700 Td (R1 10k) Tj ET ")
    pdf = _drawing(b"")
    damaged = listing[:-1] + bytes([listing[-1] ^ 0xFF])
    path = tmp_path / "damaged.pdf"
    path.write_bytes(pdf.replace(listing, damaged))
    assert path.read_bytes() != pdf
    result = parse_pdf(str(path), "Damaged")
    assert {r for p in result.inventory.values() for r in p.refs} == {"R1"}
