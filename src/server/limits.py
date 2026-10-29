"""What keeps one server's work bounded: request size, rate, OCR, memory.

The API is stateless; nothing here holds state a request depends on. The
reading cache only saves work: a miss reads the document again. Recent bugs
only add their tracebacks to reports: a miss leaves the report's code, which
names the traceback in the log.
"""

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.bom_lib import ParseResult
from src.bom_lib.errors import Bug


class BodyTooLargeError(Exception):
    """The request body is over the server's limit."""


class BodySizeLimit:
    """ASGI middleware that rejects request bodies over a byte limit.

    It checks `Content-Length` up front, and counts the bytes as they arrive
    for bodies sent without one.

    Args:
        app: The application to wrap.
        max_bytes: The largest body accepted.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serves the request, or answers 413 if its body is too large."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > self.max_bytes
        ):
            await _send_too_large(send, self.max_bytes)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLargeError
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLargeError:
            await _send_too_large(send, self.max_bytes)


async def _send_too_large(send: Send, max_bytes: int) -> None:
    body = (
        f'{{"message":"The upload is over the {max_bytes // 1_000_000} MB limit."}}'
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RateLimiter:
    """Allows each client a number of uses per sliding window.

    It's in memory, so each server instance counts separately: it stops one
    client from monopolizing an instance, while the deployment's instance cap
    bounds total cost.

    Args:
        limit: Uses allowed per client in any window.
        window: The window, in seconds.
        clock: Seconds, monotonic; injectable for tests.
    """

    def __init__(
        self,
        limit: int,
        window: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        """Records a request from the client, if it's within the limit.

        Args:
            client: The client's address.

        Returns:
            Whether the request may go ahead.
        """
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            # Forget idle clients, so the table can't grow without bound.
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True


class OcrQueue:
    """Limits how many documents are read by OCR at once.

    OCR takes both of an instance's cores and over 1 GB for tens of seconds a
    document, so a server runs one at a time and queues the rest. Text reads
    take a fraction of a second and never wait here.

    Args:
        slots: Documents read by OCR at once.
    """

    def __init__(self, slots: int) -> None:
        self._slots = threading.BoundedSemaphore(slots)
        self._lock = threading.Lock()
        self._waiting = 0

    def queue_position(self) -> int:
        """The position a job would take in the queue, 0 if a slot is free."""
        with self._lock:
            return 0 if self._free() else self._waiting + 1

    def _free(self) -> bool:
        if self._slots.acquire(blocking=False):
            self._slots.release()
            return True
        return False

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Waits for a free slot and holds it while the block runs."""
        with self._lock:
            self._waiting += 1
        self._slots.acquire()
        with self._lock:
            self._waiting -= 1
        try:
            yield
        finally:
            self._slots.release()


class ReadingCache:
    """Recent readings of uploads and links, by document identity.

    Each master list request carries its documents again; this spares
    reading them again, which matters most for OCR. Bounded by the size of
    the documents it holds.

    Args:
        max_bytes: The documents' total size to keep.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, ParseResult] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    @staticmethod
    def _bytes(reading: ParseResult) -> int:
        # The document's bytes dominate; count a floor for links' readings.
        return max(len(reading.raw_content or b""), 64_000)

    def get(self, key: str) -> ParseResult | None:
        """The reading for a document, if it's cached.

        Args:
            key: The document's identity (`src.api.document_key`).

        Returns:
            The reading, or None.
        """
        with self._lock:
            reading = self._entries.get(key)
            if reading is not None:
                self._entries.move_to_end(key)
            return reading

    def put(self, key: str, reading: ParseResult) -> None:
        """Caches a document's reading, evicting the least recently used.

        Args:
            key: The document's identity (`src.api.document_key`).
            reading: Its reading.
        """
        size = self._bytes(reading)
        if size > self.max_bytes:
            return
        with self._lock:
            if key in self._entries:
                self._size -= self._bytes(self._entries.pop(key))
            self._entries[key] = reading
            self._size += size
            while self._size > self.max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._size -= self._bytes(evicted)


class RecentBugs:
    """The bugs this server showed builders lately, by code.

    A builder's report sends back only a bug's code; this finds its traceback
    for the report, so the traceback never travels to the browser.

    Args:
        max_bugs: How many to keep.
    """

    def __init__(self, max_bugs: int = 256) -> None:
        self.max_bugs = max_bugs
        self._bugs: OrderedDict[str, Bug] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, bug: Bug) -> None:
        """Keeps a bug, forgetting the oldest beyond the limit."""
        with self._lock:
            self._bugs[bug.code] = bug
            self._bugs.move_to_end(bug.code)
            while len(self._bugs) > self.max_bugs:
                self._bugs.popitem(last=False)

    def get(self, code: str) -> Bug | None:
        """The bug with this code, if this server still holds it."""
        with self._lock:
            return self._bugs.get(code)
