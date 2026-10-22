"""Fetching a BOM document from a URL a user typed.

The hosted app fetches on the user's behalf, so a URL must not reach the
server's own network: only http(s) to public addresses, checked again at each
redirect, and only as much as a parts document needs.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

MAX_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT_S = 10


class FetchError(ValueError):
    """A URL the app won't fetch, or a response it won't read."""


def _check_public(url: str) -> None:
    """Raises FetchError unless the URL is http(s) on public addresses only."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"Only http(s) links can be fetched: {url}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except socket.gaierror as e:
        raise FetchError(f"Can't find the host {parts.hostname}") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise FetchError(f"{parts.hostname} is not a public address")


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
        _check_public(url)
        with requests.get(
            url, timeout=TIMEOUT_S, stream=True, allow_redirects=False
        ) as response:
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
