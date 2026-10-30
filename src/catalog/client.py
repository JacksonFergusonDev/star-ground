"""The catalog job's only way to reach a publisher's site.

It is deliberately slow and plain:

- **Identified:** every request says it's Star Ground and links to the site.
  It never imitates a browser.
- **Allowed:** each site's robots.txt is read first, and a URL it disallows
  isn't requested.
- **Spaced out:** requests to one host wait for the site's `Crawl-delay`, and
  never less than `MIN_DELAY_S` plus up to `JITTER_S` more at random
  (docs.pedalpcb.com asks for 10 seconds). The job runs nightly, so going
  slowly costs nothing and is less likely to be turned away.
- **Patient:** a 429 or a server error is waited out (its `Retry-After`, or
  `BACKOFF_S`) and tried again, up to `RETRIES` times.
- **Stopped by a bot check:** a challenge (Cloudflare's, a 403, or a 429 that
  persists) ends the run for that site (`BlockedError`). Getting around it
  isn't attempted.
- **Conditional:** a document fetched before is asked for with its `ETag`
  and `Last-Modified`, so an unchanged one costs a 304 and no body.
"""

import logging
import random
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

import requests

SITE = "https://star-ground.jacksonferguson.me"
# The least wait between two requests to one host, in seconds, and up to how
# much more at random, so the requests don't arrive on a beat.
MIN_DELAY_S = 15.0
JITTER_S = 5.0
# How long to wait out a 429 or a server error that gives no Retry-After, and
# how many times to try again: one minute, then two, then four.
BACKOFF_S = 60.0
RETRIES = 3
# A build document is at most a few tens of megabytes.
MAX_BYTES = 40 * 1024 * 1024
_TIMEOUT_S = (10.0, 60.0)

logger = logging.getLogger(__name__)


def user_agent() -> str:
    """How every request introduces itself."""
    try:
        release = version("star-ground")
    except PackageNotFoundError:
        release = "dev"
    return f"StarGround/{release} (+{SITE}; circuit catalog)"


class BlockedError(Exception):
    """A site turned the job away (a bot check, or 403 or 429).

    The job stops asking that site for this run; it never works around it.
    """


class GoneError(Exception):
    """A link that's dead: its host no longer exists, or it answers 404 or 410.

    Not a failure to look into: the document is no longer published there.
    """


class DisallowedError(Exception):
    """A URL the site's robots.txt asks robots not to request."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """A response the job read.

    Attributes:
        url: The URL asked for.
        status: 200, or 304 when a conditional request found it unchanged.
        content: The body; empty for a 304.
        etag: The `ETag` to send next time, if the site gave one.
        last_modified: The `Last-Modified` to send next time, if given.
    """

    url: str
    status: int
    content: bytes
    etag: str | None
    last_modified: str | None

    @property
    def text(self) -> str:
        """The body as text, for HTML and XML."""
        return self.content.decode("utf-8", errors="replace")


def _challenged(response: requests.Response) -> bool:
    """Whether the site answered with a bot check, which is never worked around."""
    if response.headers.get("cf-mitigated") == "challenge":
        return True
    return response.status_code == 403


def _retry_after(response: requests.Response, attempt: int) -> float | None:
    """How long to wait before trying again, or None when it's not worth it."""
    if response.status_code != 429 and response.status_code < 500:
        return None
    stated = response.headers.get("Retry-After", "")
    if stated.isdigit():
        return float(stated)
    return float(BACKOFF_S * 2**attempt)


class PoliteClient:
    """Fetches from publishers' sites as the catalog job, politely.

    Args:
        session: The HTTP session (tests pass a fake).
        sleep: Waits; tests pass a fake to run instantly.
        clock: The current time in seconds.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        delay: float = MIN_DELAY_S,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent()
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last: dict[str, float] = {}
        self._blocked: set[str] = set()
        self._delay = delay
        self._requests = 0
        self._companions: list[PoliteClient] = []

    @property
    def request_count(self) -> int:
        """How many requests this client and its companions have made."""
        return self._requests + sum(c.request_count for c in self._companions)

    def alongside(self) -> PoliteClient:
        """A client for another thread: the same session, waits and spacing.

        It keeps its own per-host timing, so it suits a thread that asks
        other hosts than this client's (PedalPCB's pages beside its
        documents). Sharing the session keeps a test's fake web in use.
        """
        companion = PoliteClient(self._session, self._sleep, self._clock, self._delay)
        self._companions.append(companion)
        return companion

    def _wait(self, host: str, delay: float) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = last + delay + random.uniform(0, JITTER_S) - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()

    def _rules(self, origin: str) -> urllib.robotparser.RobotFileParser:
        rules = self._robots.get(origin)
        if rules is None:
            rules = urllib.robotparser.RobotFileParser(f"{origin}/robots.txt")
            response = self._request(f"{origin}/robots.txt", {}, self._delay)
            if response.status_code == 200:
                rules.parse(response.text.splitlines())
            else:
                # No robots.txt (Tonepad answers 404): nothing is disallowed.
                rules.parse([])
            self._robots[origin] = rules
        return rules

    def _request(
        self, url: str, headers: dict[str, str], delay: float
    ) -> requests.Response:
        host = urlsplit(url).netloc
        if host in self._blocked:
            raise BlockedError(
                f"{host} turned the catalog job away earlier in this run"
            )
        for attempt in range(RETRIES + 1):
            self._wait(host, delay)
            self._requests += 1
            logger.debug("GET %s", url)
            try:
                response = self._session.get(
                    url, headers=headers, timeout=_TIMEOUT_S, stream=True
                )
            except requests.exceptions.ConnectionError as error:
                if "NameResolutionError" in repr(error):
                    raise GoneError(
                        f"{host} no longer exists (its name doesn't resolve)"
                    ) from None
                raise ConnectionError(f"couldn't connect to {host}") from error
            if _challenged(response):
                self._blocked.add(host)
                response.close()
                raise BlockedError(
                    f"{host} answered {url} with a bot check "
                    f"({response.status_code}); skipping {host} for this run"
                )
            wait = _retry_after(response, attempt)
            if wait is None or attempt == RETRIES:
                break
            logger.warning(
                "%s answered %s to %s; waiting %.0f s before trying again (%d of %d)",
                host,
                response.status_code,
                url,
                wait,
                attempt + 1,
                RETRIES,
            )
            response.close()
            self._sleep(wait)
        if response.status_code == 429:
            self._blocked.add(host)
            response.close()
            raise BlockedError(
                f"{host} kept answering 429 (too many requests); skipping {host} "
                "for this run"
            )
        return response

    def get(
        self, url: str, etag: str | None = None, last_modified: str | None = None
    ) -> Fetched:
        """Fetches a URL, if the site allows it.

        Args:
            url: The page or document.
            etag: The `ETag` from the last fetch, to ask whether it changed.
            last_modified: The `Last-Modified` from the last fetch.

        Returns:
            The response, or a 304 with no body when it's unchanged.

        Raises:
            DisallowedError: robots.txt disallows the URL.
            BlockedError: The site turned the job away.
            requests.HTTPError: Any other failure status.
        """
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules = self._rules(origin)
        agent = user_agent()
        if not rules.can_fetch(agent, url):
            raise DisallowedError(f"robots.txt disallows {url}")
        delay = max(self._delay, float(rules.crawl_delay(agent) or 0))
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        with self._request(url, headers, delay) as response:
            if response.status_code == 304:
                return Fetched(url, 304, b"", etag, last_modified)
            if response.status_code in (404, 410):
                raise GoneError(
                    f"{url} answers {response.status_code}: it's no longer there"
                )
            response.raise_for_status()
            content = bytearray()
            try:
                for chunk in response.iter_content(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError(f"{url} is larger than {MAX_BYTES} bytes")
            except requests.exceptions.ChunkedEncodingError:
                # Tonepad's server sometimes states a longer length than it
                # sends. A body that ends with the PDF end marker is whole;
                # the parser still has to read every page of it.
                if not bytes(content).rstrip().endswith(b"%%EOF"):
                    raise
            return Fetched(
                url,
                response.status_code,
                bytes(content),
                response.headers.get("ETag"),
                response.headers.get("Last-Modified"),
            )
