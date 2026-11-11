"""How much a PDF page's drawing instructions decode to, up to a bound.

A page is drawn by its content streams and the forms they draw. pdfminer
decodes each stream whole before reading it, then keeps every character it
draws, at about 1.8 KB each. Streams compress about a thousandfold, and a
crafted one far more, so a small upload can ask for gigabytes before a
page's characters can be counted. `content_bytes` decodes a page's streams
a piece at a time and stops as soon as they pass the bound.
"""

import io
import re
import zlib
from collections.abc import Iterator, Mapping
from typing import Any

from pdfminer.ascii85 import ascii85decode, asciihexdecode
from pdfminer.lzw import LZWDecoder
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import (
    LITERALS_ASCII85_DECODE,
    LITERALS_ASCIIHEX_DECODE,
    LITERALS_FLATE_DECODE,
    LITERALS_LZW_DECODE,
    PDFStream,
    resolve1,
)
from pdfminer.psparser import LIT

from star_ground.bom_lib.errors import UnreadablePdfError

_FORM = LIT("Form")
# A form drawn by name: '/Fm0 Do'.
_DRAW_FORM = re.compile(rb"/([^\s/\[\]()<>{}%]+)\s*Do(?![^\s/\[\]()<>{}%])")
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
# Compressed input is fed this many bytes at a time, so a stream that turns
# out damaged loses at most this much input's output from the count (see
# `_inflate`). Deflate expands a byte at most about 1,032 times.
_INFLATE_STEP = 64
_MAX_INFLATE_RATIO = 1032


def content_bytes(page: PDFPage, bound: int) -> int | None:
    """How many bytes the page's content streams decode to, forms included.

    Each form the page draws (by name, from its content) is counted once,
    however often it's drawn, so this bounds the instructions pdfminer
    decodes, not the times it runs them.

    Args:
        page: The page, as pdfminer reads its structure.
        bound: The most bytes to decode.

    Returns:
        The bytes, or None when they pass the bound, or when a stream is
        encoded in a way whose size can't be known without decoding it all.

    Raises:
        UnreadablePdfError: An encrypted stream is damaged.
    """
    total = 0
    seen: set[int] = set()
    todo: list[tuple[Any, Any]] = [
        (stream, page.resources) for stream in reversed(page.contents)
    ]
    while todo:
        item, resources = todo.pop()
        stream = resolve1(item)
        if not isinstance(stream, PDFStream) or id(stream) in seen:
            continue
        seen.add(id(stream))
        data = _decoded(stream, bound - total)
        if data is None:
            return None
        total += len(data)
        forms = _forms(resources)
        for name in _drawn_forms(data):
            form = resolve1(forms.get(name))
            if isinstance(form, PDFStream) and form.get("Subtype") is _FORM:
                # A form without resources uses those of what draws it.
                todo.append((form, resolve1(form.get("Resources")) or resources))
    return total


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


def _decoded(stream: PDFStream, bound: int) -> bytes | None:
    """The stream's data as pdfminer decodes it, or None past `bound` bytes.

    Decoded the same way, filter by filter, but a piece at a time. Its
    predictors, which never lengthen data, are left out.
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
    for name, _ in stream.get_filters():
        if name in LITERALS_FLATE_DECODE:
            inflated = _inflate(data, bound)
        elif name in LITERALS_LZW_DECODE:
            inflated = _unlzw(data, bound)
        elif name in LITERALS_ASCII85_DECODE:
            inflated = ascii85decode(data)  # Shorter than its input.
        elif name in LITERALS_ASCIIHEX_DECODE:
            inflated = asciihexdecode(data)  # Shorter than its input.
        else:
            # No build document's drawing is encoded any other way.
            return None
        if inflated is None:
            return None
        data = inflated
    return data if len(data) <= bound else None


def _inflate(data: bytes, bound: int) -> bytes | None:
    """Flate data decompressed, or None past `bound` bytes.

    pdfminer reads a damaged stream up to the damage, so a stream that fails
    to decompress counts what came out before the failing piece of input,
    plus the most that piece could have made.
    """
    inflater = zlib.decompressobj()
    out = bytearray()
    for start in range(0, len(data), _INFLATE_STEP):
        piece = data[start : start + _INFLATE_STEP]
        try:
            while piece and len(out) <= bound:
                out += inflater.decompress(piece, bound + 1 - len(out))
                piece = inflater.unconsumed_tail
        except zlib.error:
            lost = _MAX_INFLATE_RATIO * _INFLATE_STEP
            return bytes(out) if len(out) + lost <= bound else None
        if len(out) > bound:
            return None
        if inflater.eof:
            break
    return bytes(out)


def _unlzw(data: bytes, bound: int) -> bytes | None:
    """LZW data decompressed by pdfminer's decoder, or None past `bound` bytes."""
    out = bytearray()
    for piece in LZWDecoder(io.BytesIO(data)).run():
        out += piece
        if len(out) > bound:
            return None
    return bytes(out)
