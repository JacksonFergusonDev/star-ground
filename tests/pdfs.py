"""Small PDFs for tests: typeset with Typst and Star Ground's own fonts, or
assembled object by object."""

import json
import struct
import zlib
from hashlib import md5
from pathlib import Path

import typst
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher
from pdfminer.pdfdocument import PDFStandardSecurityHandler

FONTS = Path(__file__).parent.parent / "src" / "star_ground" / "exports" / "fonts"


def typeset(source: str) -> bytes:
    """A PDF from Typst markup, using only the fonts the exports embed."""
    # typst's stubs type `input` and `font_paths` with one type variable,
    # which bytes and a path string can't share.
    pdf: bytes = typst.compile(  # type: ignore[misc]
        source.encode(), font_paths=[str(FONTS)], ignore_system_fonts=True
    )
    return pdf


def text_pdf(*lines: str, pages: int = 1, size: int = 14) -> bytes:
    """A US Letter PDF with each line as its own paragraph on the first page.

    Args:
        lines: The text, line by line.
        pages: How many pages in all; the rest are blank.
        size: The text's size in points.

    Returns:
        The PDF's bytes.
    """
    body = "\n\n".join(f"#{json.dumps(line)}" for line in lines)
    breaks = "\n#pagebreak()\n" * (pages - 1)
    return typeset(
        '#set page(paper: "us-letter")\n'
        f'#set text(font: "DM Sans 14pt", size: {size}pt)\n'
        f"{body}{breaks}"
    )


def assemble(*objects: bytes, trailer: str = "") -> bytes:
    """A PDF of the objects, numbered from 1, with its cross-reference table.

    Object 1 is the catalog. For files a typesetter won't write: damaged ones,
    and ones built to ask too much of a reader.

    Args:
        objects: Each object's body, from `<<` to `>>` (and its stream).
        trailer: More trailer entries ('/Encrypt << … >>').

    Returns:
        The PDF's bytes.
    """
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {trailer}>>\n".encode()
    return out + f"startxref\n{xref}\n%%EOF\n".encode()


def stream(data: bytes, entries: str = "", *, compress: bool = True) -> bytes:
    """A stream object's body holding the data, Flate-compressed by default.

    Args:
        data: The stream's decoded data.
        entries: More dictionary entries ('/Subtype /Form').
        compress: Whether to compress it.
    """
    raw = zlib.compress(data) if compress else data
    flate = " /Filter /FlateDecode" if compress else ""
    head = f"<< /Length {len(raw)}{flate} {entries}>>\nstream\n".encode()
    return head + raw + b"\nendstream"


def _rc4(key: bytes, data: bytes) -> bytes:
    encryptor = Cipher(ARC4(key), mode=None).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def encrypted(*objects: bytes) -> bytes:
    """`assemble`, with each stream encrypted as an RC4 128-bit PDF's are.

    Standard encryption, revision 3, opened with no password, as most
    encrypted build documents are. The objects' dictionaries must hold no
    strings, which would be encrypted too.

    Args:
        objects: Each object's body, from `<<` to `>>` (and its stream).

    Returns:
        The PDF's bytes.
    """
    padding = PDFStandardSecurityHandler.PASSWORD_PADDING
    owner, permissions, docid = bytes(32), -4, b"star-ground-test"
    # The file's key and the check of it ('/U'): algorithms 3.2 and 3.5.
    key = md5(padding + owner + struct.pack("<l", permissions) + docid).digest()
    for _ in range(50):
        key = md5(key[:16]).digest()
    key = key[:16]
    check = _rc4(key, md5(padding + docid).digest())
    for i in range(1, 20):
        check = _rc4(bytes(c ^ i for c in key), check)
    bodies = []
    for number, body in enumerate(objects, start=1):
        head, mark, rest = body.partition(b"stream\n")
        if mark:
            data, end = rest.rsplit(b"\nendstream", 1)
            object_key = md5(key + struct.pack("<L", number)[:3] + b"\0\0").digest()
            body = head + mark + _rc4(object_key, data) + b"\nendstream" + end
        bodies.append(body)
    return assemble(
        *bodies,
        trailer=(
            "/Encrypt << /Filter /Standard /V 2 /R 3 /Length 128 "
            f"/P {permissions} /O <{owner.hex()}> /U <{(check + bytes(16)).hex()}> >> "
            f"/ID [<{docid.hex()}> <{docid.hex()}>] "
        ),
    )
