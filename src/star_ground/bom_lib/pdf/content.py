"""How much a PDF page's drawing instructions run, and what a stream decodes to.

A page is drawn by its content streams and the forms they draw. pdfminer
decodes each stream whole before reading it, then keeps every character it
draws, at about 1.8 KB each. Streams compress about a thousandfold, and a
crafted one far more, so a small upload can ask for gigabytes before a
page's characters can be counted. `decoded` decodes a stream a piece at a
time and stops as soon as it passes a bound; `content_bytes` adds up what a
page runs, a form once for every time it's drawn.
"""

import io
import re
import zlib
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from pdfminer.ascii85 import ascii85decode, asciihexdecode
from pdfminer.lzw import LZWDecoder
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import (
    LITERALS_ASCII85_DECODE,
    LITERALS_ASCIIHEX_DECODE,
    LITERALS_FLATE_DECODE,
    LITERALS_LZW_DECODE,
    LITERALS_RUNLENGTH_DECODE,
    PDFStream,
    resolve1,
)
from pdfminer.psparser import LIT

from star_ground.bom_lib.errors import UnreadablePdfError

_FORM = LIT("Form")
# A form drawn by name: '/Fm0 Do'.
_DRAW_FORM = re.compile(rb"/([^\s/\[\]()<>{}%]+)\s*Do(?![^\s/\[\]()<>{}%])")
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
# Compressed input is fed this many bytes at a time.
_INFLATE_STEP = 4096
# Forms drawn within forms, at most this deep. Real documents nest a few.
_MAX_FORM_DEPTH = 16


class OpaqueStreamError(Exception):
    """A stream encoded in a way whose size can't be known without decoding it."""


def content_bytes(page: PDFPage, bound: int) -> int | None:
    """How many bytes of drawing instructions the page runs, forms included.

    A form counts every time it's drawn, however deeply, because pdfminer
    runs it every time: the characters it draws are kept once for each. Its
    stream is decoded once, so a form drawn thousands of times, or forms
    drawing forms, ask for far more than the bytes in the file.

    Args:
        page: The page, as pdfminer reads its structure.
        bound: The most bytes to run.

    Returns:
        The bytes, or None when they pass the bound, when forms draw
        themselves or nest too deep, or when a stream is encoded in a way
        whose size can't be known without decoding it all.

    Raises:
        UnreadablePdfError: An encrypted stream is damaged.
    """
    # What each form runs once, by the form and the resources it draws with.
    memo: dict[tuple[int, int], int | None] = {}
    active: set[tuple[int, int]] = set()

    def run(item: Any, resources: Any, depth: int) -> int | None:
        stream = resolve1(item)
        if not isinstance(stream, PDFStream):
            return 0
        resources = resolve1(resources)
        key = (id(stream), id(resources))
        if key in memo:
            return memo[key]
        if key in active or depth > _MAX_FORM_DEPTH:
            return None  # A form drawing itself runs for ever.
        active.add(key)
        total: int | None = _forms_run(stream, resources, depth)
        active.discard(key)
        memo[key] = total
        return total

    def _forms_run(stream: PDFStream, resources: Any, depth: int) -> int | None:
        try:
            data = decoded(stream, bound)
        except OpaqueStreamError:
            return None
        if data is None:
            return None
        total = len(data)
        forms = _forms(resources)
        for name, times in Counter(_drawn_forms(data)).items():
            form = resolve1(forms.get(name))
            if not (isinstance(form, PDFStream) and form.get("Subtype") is _FORM):
                continue
            # A form without resources uses those of what draws it.
            inner = run(form, resolve1(form.get("Resources")) or resources, depth + 1)
            if inner is None:
                return None
            total += times * inner
            if total > bound:
                return None
        return total if total <= bound else None

    whole = 0
    for stream in page.contents:
        ran = run(stream, page.resources, 0)
        if ran is None:
            return None
        whole += ran
        if whole > bound:
            return None
    return whole


def _forms(resources: object) -> Mapping[Any, Any]:
    """The named XObjects in a resource dictionary, if it has any."""
    resources = resolve1(resources)
    xobjects = (
        resolve1(resources.get("XObject")) if isinstance(resources, dict) else None
    )
    return xobjects if isinstance(xobjects, dict) else {}


def _drawn_forms(data: bytes) -> Iterator[str | bytes]:
    """The names a content stream draws with 'Do', as pdfminer spells them."""
    for match in _DRAW_FORM.finditer(data):
        name = _NAME_ESCAPE.sub(lambda m: bytes.fromhex(m.group(1).decode()), match[1])
        try:
            yield name.decode("utf-8")
        except UnicodeDecodeError:
            yield name


def decoded(
    stream: PDFStream,
    bound: int,
    mend: Callable[[int, bytes], None] | None = None,
) -> bytes | None:
    """The stream's data as pdfminer decodes it, or None past `bound` bytes.

    Decoded the same way, filter by filter, but a piece at a time. Its
    predictors, which never lengthen data, are left out.

    Args:
        stream: The stream.
        bound: The most bytes any filter may make.
        mend: Called with a filter's number (from 0) and what it made when
            Flate data is damaged or truncated.

    Raises:
        OpaqueStreamError: A filter's output can't be bounded without
            decoding it (images, mostly).
        UnreadablePdfError: An encrypted stream is damaged.
    """
    if stream.data is not None:  # Decoded already, for another page.
        return stream.data if len(stream.data) <= bound else None
    data = stream.get_rawdata() or b""
    if stream.decipher and stream.objid is not None and stream.genno is not None:
        try:
            data = stream.decipher(stream.objid, stream.genno, data, stream.attrs)
        except ValueError as e:
            # An encrypted stream too short to hold its AES key's start: a
            # damaged file, which pdfminer can't read either.
            raise UnreadablePdfError from e
    for step, (name, _) in enumerate(stream.get_filters()):
        if name in LITERALS_FLATE_DECODE:
            flate = inflate(data, bound)
            inflated = None if flate is None else flate[0]
            if flate is not None and not flate[1] and mend is not None:
                mend(step, flate[0])
        elif name in LITERALS_LZW_DECODE:
            inflated = _unlzw(data, bound)
        elif name in LITERALS_RUNLENGTH_DECODE:
            inflated = _unrun(data, bound)
        elif name in LITERALS_ASCII85_DECODE:
            inflated = ascii85decode(data)  # Shorter than its input.
        elif name in LITERALS_ASCIIHEX_DECODE:
            inflated = asciihexdecode(data)  # Shorter than its input.
        else:
            raise OpaqueStreamError
        if inflated is None:
            return None
        data = inflated
    return data if len(data) <= bound else None


def _feed(inflater: Any, piece: bytes, out: bytearray, bound: int) -> bool:
    """Decompresses a piece into `out`; False once `out` passes `bound`."""
    while piece:
        out += inflater.decompress(piece, bound + 1 - len(out))
        if len(out) > bound:
            return False
        piece = inflater.unconsumed_tail
    return True


def inflate(data: bytes, bound: int) -> tuple[bytes, bool] | None:
    """Flate data as pdfminer decompresses it, or None past `bound` bytes.

    pdfminer reads a stream that is damaged only at its checksum, or
    truncated, up to the damage, and one damaged earlier as empty. Its way
    of doing so feeds the decompressor a byte at a time and joins the pieces
    by copying them all, which takes quadratic time; this takes the same
    result in linear time.

    Returns:
        The data and whether the stream was whole, or None past the bound.
    """
    inflater = zlib.decompressobj()
    out = bytearray()
    try:
        if not _feed(inflater, data, out, bound):
            return None
    except zlib.error:
        mended = _corrupted(data, bound)
        return None if mended is None else (mended, False)
    return bytes(out), inflater.eof


def _corrupted(data: bytes, bound: int) -> bytes | None:
    """What `pdfminer.pdftypes.decompress_corrupted` returns, in linear time."""
    inflater = zlib.decompressobj()
    out = bytearray()
    for start in range(0, len(data), _INFLATE_STEP):
        piece = data[start : start + _INFLATE_STEP]
        saved = inflater.copy()
        mark = len(out)
        try:
            if not _feed(inflater, piece, out, bound):
                return None
        except zlib.error:
            # Found again a byte at a time, to say which byte it was.
            del out[mark:]
            inflater = saved
            for k, byte in enumerate(piece, start=start):
                try:
                    if not _feed(inflater, bytes([byte]), out, bound):
                        return None
                except zlib.error:
                    # Damage in the checksum, the last bytes, is let pass.
                    return bytes(out) if k >= len(data) - 3 else b""
    return bytes(out)


def _unlzw(data: bytes, bound: int) -> bytes | None:
    """LZW data decompressed by pdfminer's decoder, or None past `bound` bytes."""
    out = bytearray()
    for piece in LZWDecoder(io.BytesIO(data)).run():
        out += piece
        if len(out) > bound:
            return None
    return bytes(out)


def _unrun(data: bytes, bound: int) -> bytes | None:
    """Run-length data decoded as pdfminer's `rldecode` does, or None past `bound`."""
    out = bytearray()
    i = 0
    while i < len(data):
        length = data[i]
        if length == 128:
            break
        if length < 128:
            out += data[i + 1 : i + length + 2]
            i += length + 2
        else:
            out += data[i + 1 : i + 2] * (257 - length)
            i += 2
        if len(out) > bound:
            return None
    return bytes(out)
