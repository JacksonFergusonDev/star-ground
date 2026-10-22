"""Fetching a BOM document from a URL a user typed.

The hosted app fetches on the user's behalf, so a URL must not reach the
server's own network: only http(s) to public addresses, checked again at each
redirect, and only as much as a parts document needs. The address that passed
the check is the one connected to, so the host can't resolve somewhere else
between the check and the fetch (DNS rebinding).
"""

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

MAX_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_S = 10

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchError(ValueError):
    """A URL the app won't fetch, or a response it won't read."""


def _public_address(url: str) -> IPAddress:
    """Resolves a URL's host, requiring every address it has to be public.

    Args:
        url: An absolute URL.

    Returns:
        The address to connect to.

    Raises:
        FetchError: The URL isn't http(s), its host doesn't resolve, or any of
            its addresses is not public.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"Only http(s) links can be fetched: {url}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except socket.gaierror as e:
        raise FetchError(f"Can't find the host {parts.hostname}") from e
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses or not all(address.is_global for address in addresses):
        raise FetchError(f"{parts.hostname} is not a public address")
    return addresses[0]


def _pinned_url(url: str, address: IPAddress) -> str:
    """Returns the URL with its host replaced by a resolved address."""
    parts = urlsplit(url)
    host = f"[{address}]" if address.version == 6 else str(address)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc).geturl()


class _PinnedTLSAdapter(HTTPAdapter):
    """Connects to an address but checks TLS against the URL's hostname.

    With the URL's host replaced by an address, TLS would otherwise send that
    address as the server name and verify the certificate against it.
    """

    def __init__(self, hostname: str) -> None:
        """Initializes the adapter.

        Args:
            hostname: The hostname to send as SNI and verify the certificate
                against.
        """
        self._hostname = hostname
        super().__init__(max_retries=0)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        """Gives every connection pool the hostname for TLS."""
        super().init_poolmanager(
            connections, maxsize, block, server_hostname=self._hostname, **pool_kwargs
        )


@contextmanager
def _get(url: str, address: IPAddress) -> Iterator[requests.Response]:
    """Streams a GET of the URL, connected to the given address.

    Proxies from the environment are ignored: a proxy would resolve the host
    again itself.
    """
    parts = urlsplit(url)
    with requests.Session() as session:
        session.trust_env = False
        session.mount("https://", _PinnedTLSAdapter(parts.hostname or ""))
        with session.get(
            _pinned_url(url, address),
            headers={"Host": parts.netloc.rpartition("@")[2]},
            timeout=TIMEOUT_S,
            stream=True,
            allow_redirects=False,
        ) as response:
            yield response


def fetch_url(url: str) -> bytes:
    """Downloads a public http(s) URL, following redirects that stay public.

    Args:
        url: The URL as typed.

    Returns:
        The response body.

    Raises:
        FetchError: The URL or a redirect leaves the public internet, there
            are too many redirects, or the body exceeds MAX_BYTES.
        requests.RequestException: The request itself failed.
    """
    for _ in range(MAX_REDIRECTS + 1):
        address = _public_address(url)
        with _get(url, address) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body += chunk
                if len(body) > MAX_BYTES:
                    raise FetchError(
                        f"The document is over {MAX_BYTES // (1024 * 1024)} MB"
                    )
            return bytes(body)
    raise FetchError(f"Too many redirects from {url}")
