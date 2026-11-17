"""Decrypts an RC4-encrypted PDF in compiled code rather than pdfminer's Python.

pdfminer decrypts RC4 (the cipher of PDFs encrypted with 40- and 128-bit
keys) with `pdfminer.arcfour`, which builds its output a byte at a time and
takes time that grows with the square of a stream's length: Tonepad Rebote
3 Delay's 3 MB page takes about 90 seconds. `cryptography`, which pdfminer
already uses for AES, does it in OpenSSL in milliseconds. `CryptDocument`
is pdfminer's document with security handlers that decrypt RC4 that way.
"""

import struct
from hashlib import md5
from typing import ClassVar

from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
from cryptography.hazmat.primitives.ciphers import Cipher
from pdfminer.pdfdocument import (
    PDFDocument,
    PDFStandardSecurityHandler,
    PDFStandardSecurityHandlerV4,
    PDFStandardSecurityHandlerV5,
)


def rc4(key: bytes, data: bytes) -> bytes:
    """The data RC4-encrypted (or decrypted, the same thing) with the key.

    OpenSSL's RC4 takes keys of 5, 7, 8, 10, 16, 20, 24 or 32 bytes. A
    document's keys are 10 bytes (40-bit encryption) or 16 (128-bit); one
    of another length, which no corpus document uses, is decrypted in
    Python, in time that grows with the data.
    """
    if len(key) * 8 in ARC4.key_sizes:
        decryptor = Cipher(ARC4(key), mode=None).decryptor()
        return decryptor.update(data) + decryptor.finalize()
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]
    out = bytearray(data)
    i = j = 0
    for n in range(len(out)):
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        out[n] ^= state[(state[i] + state[j]) & 0xFF]
    return bytes(out)


def _object_rc4(file_key: bytes | None, objid: int, genno: int, data: bytes) -> bytes:
    """An object's data decrypted with its RC4 key, as pdfminer derives it."""
    assert file_key is not None  # Set once the password is accepted.
    key = file_key + struct.pack("<L", objid)[:3] + struct.pack("<L", genno)[:2]
    return rc4(md5(key).digest()[: min(len(key), 16)], data)


class _Handler(PDFStandardSecurityHandler):
    """pdfminer's handler for encryption versions 1 and 2, decrypting in C."""

    def decrypt_rc4(self, objid: int, genno: int, data: bytes) -> bytes:
        """The object's data, decrypted."""
        return _object_rc4(self.key, objid, genno, data)


class _HandlerV4(PDFStandardSecurityHandlerV4):
    """pdfminer's handler for encryption version 4, decrypting RC4 in C."""

    def decrypt_rc4(self, objid: int, genno: int, data: bytes) -> bytes:
        """The object's data, decrypted."""
        return _object_rc4(self.key, objid, genno, data)


class _HandlerV5(PDFStandardSecurityHandlerV5):
    """pdfminer's handler for encryption version 5, decrypting RC4 in C."""

    def decrypt_rc4(self, objid: int, genno: int, data: bytes) -> bytes:
        """The object's data, decrypted."""
        return _object_rc4(self.key, objid, genno, data)


class CryptDocument(PDFDocument):
    """pdfminer's document, decrypting RC4 in compiled code."""

    security_handler_registry: ClassVar[dict[int, type[PDFStandardSecurityHandler]]] = {
        1: _Handler,
        2: _Handler,
        4: _HandlerV4,
        5: _HandlerV5,
    }
