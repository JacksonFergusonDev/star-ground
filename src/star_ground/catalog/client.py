"""The catalog job's only way to reach a publisher's site.

It is deliberately slow and plain:

- **Identified:** every request says it's Star Ground and links to the site.
  It never imitates a browser.
- **Allowed:** a URL is requested only when it's on the publisher's site and
  that host's robots.txt allows it. A robots.txt that's missing (4xx) allows
  everything; one that can't be read (a server error or no answer, after the
  usual retries) stops the job asking that host for the run, as RFC 9309 asks.
- **Followed by hand:** redirects are followed one hop at a time, at most
  `MAX_REDIRECTS`, and each hop is checked like the first: on the publisher's
  site, allowed by its host's robots.txt, and spaced like any other request.
- **Spaced out:** requests to one host wait for the site's `Crawl-delay`, and
  never less than `MIN_DELAY_S` plus up to `JITTER_S` more at random
  (docs.pedalpcb.com asks for 10 seconds). The job runs nightly, so going
  slowly costs nothing and is less likely to be turned away. The spacing
  holds across threads: a client made `alongside` shares it.
- **Patient:** a 429, a server error, a timeout, a dropped connection or a
  name that doesn't resolve is waited out (a 429's `Retry-After`, or
  `BACKOFF_S`) and tried again, up to `RETRIES` times. So is a sitemap that
  comes back as something else (`discover`), as a site's error page sometimes
  does. A `Retry-After` longer than `MAX_WAIT_S` isn't waited out: the host is
  skipped for the run, so one answer can't spend the whole job's time.
- **Stopped by a bot check:** a challenge (Cloudflare's, a 403, or a 429 that
  persists) ends the run for that site (`BlockedError`). Getting around it
  isn't attempted.
- **Conditional:** a document fetched before is asked for with its `ETag`
  and `Last-Modified`, so an unchanged one costs a 304 and no body.
"""

import logging
import random
import threading
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import requests

from star_ground import version

SITE = "https://star-ground.jacksonferguson.me"
# The least wait between two requests to one host, in seconds, and up to how
# much more at random, so the requests don't arrive on a beat.
MIN_DELAY_S = 15.0
JITTER_S = 5.0
# How long to wait out a 429, a server error or no answer at all, and
# how many times to try again: one minute, then two, then four.
BACKOFF_S = 60.0
RETRIES = 3
# The longest `Retry-After` waited out. A site asking for longer is skipped
# for the run: the job's time is shared, and the next run asks again.
MAX_WAIT_S = 600.0
# How many redirects one request follows (RFC 9309 asks for at least five
# for robots.txt).
MAX_REDIRECTS = 5
# A build document is at most a few tens of megabytes.
MAX_BYTES = 40 * 1024 * 1024
# How much of a robots.txt is read: RFC 9309 asks for at least 500 KiB.
MAX_ROBOTS_BYTES = 500 * 1024
_TIMEOUT_S = (10.0, 60.0)
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

logger = logging.getLogger(__name__)

Allows = Callable[[str], bool]


def user_agent() -> str:
    """How every request introduces itself."""
    return f"StarGround/{version()} (+{SITE}; circuit catalog)"


class BlockedError(Exception):
    """A site turned the job away (a bot check, or 403 or 429).

    The job stops asking that site for this run; it never works around it.
    """


class GoneError(Exception):
    """A link that's dead: it answers 404 or 410.

    Not a failure to look into: the document is no longer published there.
    A host whose name doesn't resolve isn't gone: a resolver failing on the
    job's runner looks the same, so it's a failed fetch, tried again next
    run, and its builds stay listed.
    """


class DisallowedError(Exception):
    """A URL the job may not request: robots.txt disallows it, or it's off the site."""


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


def _redirect(response: requests.Response) -> str | None:
    """Where a response redirects to, as it says it; None if it doesn't."""
    if response.status_code not in _REDIRECTS:
        return None
    return response.headers.get("Location") or None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(slots=True)
class _Hosts:
    """What a run's clients know of each host, shared between their threads.

    Attributes:
        lock: Guards everything here but the robots locks' own work.
        last: When each host was last asked, or will be by a request
            waiting its turn (`clock` seconds).
        blocked: Hosts that turned the job away, for the rest of the run.
        robots: Each origin's robots.txt rules, once read.
        robots_locks: One lock per origin, so its robots.txt is read once.
        requests: How many requests the run has made.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    last: dict[str, float] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    robots: dict[str, urllib.robotparser.RobotFileParser] = field(default_factory=dict)
    robots_locks: dict[str, threading.Lock] = field(default_factory=dict)
    requests: int = 0


class PoliteClient:
    """Fetches from publishers' sites as the catalog job, politely.

    Args:
        session: The HTTP session (tests pass a fake).
        sleep: Waits; tests pass a fake to run instantly.
        clock: The current time in seconds.
        delay: The least wait between two requests to one host, in seconds;
            a site's `Crawl-delay` can make it longer.
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
        self._delay = delay
        self._hosts = _Hosts()

    @property
    def request_count(self) -> int:
        """How many requests this client and those alongside it have made."""
        with self._hosts.lock:
            return self._hosts.requests

    def alongside(self) -> PoliteClient:
        """A client for another thread, sharing this one's session and hosts.

        Both clients space their requests to a host as one, and a host that
        turns one away is skipped by both: PedalPCB's discovery thread and
        its reading thread both ask www.pedalpcb.com, for pages and for the
        few documents under /docs/. Sharing the session keeps a test's fake
        web in use.
        """
        companion = PoliteClient(self._session, self._sleep, self._clock, self._delay)
        companion._hosts = self._hosts
        return companion

    def back_off(self, attempt: int, why: str) -> None:
        """Waits before asking again after a bad answer, as for a server error.

        Args:
            attempt: How many tries have failed before this one, from 0.
            why: What was wrong with the answer, for the log.
        """
        wait = BACKOFF_S * 2**attempt
        logger.warning(
            "%s; waiting %.0f s before trying again (%d of %d)",
            why,
            wait,
            attempt + 1,
            RETRIES,
        )
        self._sleep(wait)

    def _wait(self, host: str, delay: float) -> None:
        """Waits for this request's turn at a host, and counts it.

        The turn is taken under the lock and waited for outside it, so two
        threads asking one host take turns rather than both going at once.
        """
        hosts = self._hosts
        with hosts.lock:
            now = self._clock()
            last = hosts.last.get(host)
            at = (
                now
                if last is None
                else max(now, last + delay + random.uniform(0, JITTER_S))
            )
            hosts.last[host] = at
            hosts.requests += 1
        if at > now:
            self._sleep(at - now)

    def _block(self, host: str, why: str) -> BlockedError:
        """Skips a host for the rest of the run; the error to raise, saying why."""
        with self._hosts.lock:
            self._hosts.blocked.add(host)
        return BlockedError(f"{why}; skipping {host} for this run")

    def _rules(self, origin: str, allows: Allows) -> urllib.robotparser.RobotFileParser:
        """An origin's robots.txt rules, read once per run."""
        hosts = self._hosts
        with hosts.lock:
            rules = hosts.robots.get(origin)
            if rules is not None:
                return rules
            lock = hosts.robots_locks.setdefault(origin, threading.Lock())
        with lock:
            with hosts.lock:
                rules = hosts.robots.get(origin)
            if rules is None:
                rules = self._read_robots(origin, allows)
                with hosts.lock:
                    hosts.robots[origin] = rules
        return rules

    def _read_robots(
        self, origin: str, allows: Allows
    ) -> urllib.robotparser.RobotFileParser:
        """Reads an origin's robots.txt, as RFC 9309 says to take what comes back.

        Raises:
            BlockedError: It can't be read (a server error or no answer after
                the retries), so nothing on the host is asked for this run.
        """
        url = f"{origin}/robots.txt"
        rules = urllib.robotparser.RobotFileParser(url)
        host = urlsplit(origin).netloc
        try:
            response = self._follow(url, {}, allows, robots=False)
        except ConnectionError as error:
            raise self._block(host, f"{url} didn't answer ({error})") from None
        except DisallowedError, requests.exceptions.TooManyRedirects:
            # Redirected off the site, or too often: as if there were none.
            rules.parse([])
            return rules
        with response:
            if response.status_code >= 500:
                raise self._block(
                    host, f"{url} answered {response.status_code}, so it can't be read"
                )
            if response.status_code != 200:
                # No robots.txt (Tonepad answers 404): nothing is disallowed.
                rules.parse([])
                return rules
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) >= MAX_ROBOTS_BYTES:
                    break
        text = bytes(body[:MAX_ROBOTS_BYTES]).decode("utf-8", errors="replace")
        rules.parse(text.splitlines())
        return rules

    def _follow(
        self, url: str, headers: dict[str, str], allows: Allows, *, robots: bool = True
    ) -> requests.Response:
        """Requests a URL, following its redirects one checked hop at a time.

        Args:
            url: The URL.
            headers: Headers for every hop.
            allows: Whether a URL is on the publisher's site.
            robots: Whether each hop must be allowed by its host's robots.txt
                (not while reading a robots.txt).

        Returns:
            The last hop's response, still open.

        Raises:
            DisallowedError: A hop is off the site or disallowed.
            BlockedError: A host turned the job away.
            requests.TooManyRedirects: More than `MAX_REDIRECTS` redirects.
        """
        agent = user_agent()
        for _ in range(MAX_REDIRECTS + 1):
            if not allows(url):
                raise DisallowedError(f"{url} isn't on the publisher's site")
            delay = self._delay
            if robots:
                rules = self._rules(_origin(url), allows)
                if not rules.can_fetch(agent, url):
                    raise DisallowedError(f"robots.txt disallows {url}")
                delay = max(delay, float(rules.crawl_delay(agent) or 0))
            response = self._request(url, headers, delay)
            target = _redirect(response)
            if target is None:
                return response
            response.close()
            logger.debug("%s redirects to %s", url, target)
            url = urljoin(url, target)
        raise requests.exceptions.TooManyRedirects(
            f"{url} redirected more than {MAX_REDIRECTS} times"
        )

    def _request(
        self, url: str, headers: dict[str, str], delay: float
    ) -> requests.Response:
        """Requests one URL, waiting out what's worth waiting out; no redirects."""
        host = urlsplit(url).netloc
        response: requests.Response | None = None
        for attempt in range(RETRIES + 1):
            with self._hosts.lock:
                blocked = host in self._hosts.blocked
            if blocked:
                raise BlockedError(
                    f"{host} turned the catalog job away earlier in this run"
                )
            response = self._attempt(url, headers, delay, attempt)
            if response is None:
                continue
            if _challenged(response):
                response.close()
                raise self._block(
                    host,
                    f"{host} answered {url} with a bot check ({response.status_code})",
                )
            wait = _retry_after(response, attempt)
            if wait is None or attempt == RETRIES:
                break
            response.close()
            if wait > MAX_WAIT_S:
                raise self._block(
                    host, f"{host} asked to wait {wait:.0f} s before asking again"
                )
            logger.warning(
                "%s answered %s to %s; waiting %.0f s before trying again (%d of %d)",
                host,
                response.status_code,
                url,
                wait,
                attempt + 1,
                RETRIES,
            )
            self._sleep(wait)
        # The last try answers or raises.
        assert response is not None
        if response.status_code == 429:
            response.close()
            raise self._block(host, f"{host} kept answering 429 (too many requests)")
        return response

    def _attempt(
        self, url: str, headers: dict[str, str], delay: float, attempt: int
    ) -> requests.Response | None:
        """One try at a URL, in its turn; None after a dropped answer, waited out.

        Raises:
            ConnectionError: The last try got no answer either.
        """
        host = urlsplit(url).netloc
        self._wait(host, delay)
        logger.debug("GET %s", url)
        try:
            return self._session.get(
                url,
                headers=headers,
                timeout=_TIMEOUT_S,
                stream=True,
                allow_redirects=False,
            )
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            if attempt == RETRIES:
                raise ConnectionError(
                    f"{host} didn't answer {url} in {RETRIES + 1} tries"
                ) from error
            # A slow or dropped answer, or a name that doesn't resolve,
            # is waited out like a server error.
            wait = BACKOFF_S * 2**attempt
            logger.warning(
                "%s didn't answer %s (%s); waiting %.0f s before trying "
                "again (%d of %d)",
                host,
                url,
                type(error).__name__,
                wait,
                attempt + 1,
                RETRIES,
            )
            self._sleep(wait)
            return None

    def get(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
        *,
        allows: Allows,
    ) -> Fetched:
        """Fetches a URL, if the site allows it.

        Args:
            url: The page or document.
            etag: The `ETag` from the last fetch, to ask whether it changed.
            last_modified: The `Last-Modified` from the last fetch.
            allows: Whether a URL is on the publisher's site
                (`Source.allows`); checked for the URL and every redirect.

        Returns:
            The response, or a 304 with no body when it's unchanged.

        Raises:
            DisallowedError: The URL, or a redirect, is off the site or
                disallowed by robots.txt.
            BlockedError: The site turned the job away.
            GoneError: It answers 404 or 410.
            requests.HTTPError: Any other failure status.
        """
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        with self._follow(url, headers, allows) as response:
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
