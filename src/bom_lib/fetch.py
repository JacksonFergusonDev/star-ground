"""Fetching a BOM document from a URL a user typed.

The hosted app fetches on the user's behalf, so a URL must not reach the
server's own network: only http(s) on the standard ports, to public addresses,
checked again at each redirect, and only as much as a parts document needs,
in bounded time. The address that passed the check is the one connected to,
so the host can't resolve somewhere else between the check and the fetch (DNS
rebinding).

Share links (GitHub, Google Drive, Dropbox) open a viewer page, not the file;
they are rewritten to the file itself before fetching.
"""

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter

from src.bom_lib.errors import FetchError

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

USER_AGENT = "StarGround/1.0 (+https://github.com/JacksonFergusonDev/star-ground)"
_CHUNK_BYTES = 64 * 1024
# IPv6 that carries an IPv4 address a gateway may route to (RFC 6052 NAT64).
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def is_public(address: IPAddress) -> bool:
    """Whether an address is on the public internet, IPv4 inside IPv6 included.

    '::ffff:127.0.0.1' (IPv4-mapped) and '64:ff9b::7f00:1' (NAT64) reach
    127.0.0.1; Python calls the NAT64 form global, so it is unwrapped here.

    Args:
        address: A resolved address.

    Returns:
        True for a global address.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped.is_global
        if address in _NAT64:
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF).is_global
    return address.is_global


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """What a fetch may reach, and how much it may take.

    Attributes:
        max_bytes: The largest body read. The largest corpus PDF is 15 MB.
        max_redirects: Redirects followed before giving up.
        connect_timeout_s: Seconds to connect to a host.
        read_timeout_s: Seconds to wait for each piece of the response.
        deadline_s: Seconds for the whole fetch, every redirect included: a
            server dribbling a byte at a time can't hold the app.
        ports: Ports a URL may name; any other is refused, so the app can't
            be used to probe a public host's services.
        allow: Which resolved addresses may be connected to.
        verify: TLS trust: True for the system's certificates, or a CA
            bundle path (tests).
    """

    max_bytes: int = 30 * 1024 * 1024
    max_redirects: int = 5
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 10.0
    deadline_s: float = 30.0
    ports: frozenset[int] = frozenset({80, 443})
    allow: Callable[[IPAddress], bool] = field(default=is_public)
    verify: bool | str = True


DEFAULT_POLICY = FetchPolicy()


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """A downloaded document.

    Attributes:
        content: The response body.
        content_type: The media type the server declared ('application/pdf'),
            lower case without parameters; '' when none.
        url: The URL the body came from, after redirects.
    """

    content: bytes
    content_type: str
    url: str


# 'https://github.com/owner/repo/blob/main/bom.csv' -> raw.githubusercontent.com.
_GITHUB_BLOB = re.compile(r"/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<path>.+)")
# 'https://drive.google.com/file/d/<id>/view?usp=sharing'.
_DRIVE_FILE = re.compile(r"/file/d/(?P<id>[\w-]+)")


def direct_link(url: str) -> str:
    """The file behind a share link, or the URL unchanged.

    Args:
        url: The URL as typed.

    Returns:
        A URL that answers with the file itself.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("github.com", "www.github.com"):
        blob = _GITHUB_BLOB.fullmatch(parts.path)
        if blob:
            return (
                f"https://raw.githubusercontent.com/{blob['owner']}/{blob['repo']}/"
                f"{blob['path']}"
            )
    if host == "drive.google.com":
        drive = _DRIVE_FILE.match(parts.path)
        if drive:
            return f"https://drive.google.com/uc?export=download&id={drive['id']}"
    if host in ("dropbox.com", "www.dropbox.com"):
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "dl"]
        return parts._replace(query=urlencode([*query, ("dl", "1")])).geturl()
    return url


def _target(url: str, policy: FetchPolicy) -> tuple[str, int, IPAddress]:
    """Checks a URL and resolves its host, requiring every address to be allowed.

    Returns:
        The hostname, the port, and the address to connect to.

    Raises:
        FetchError: The URL isn't http(s) on an allowed port, its host doesn't
            resolve, or any of its addresses is not allowed.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"Only http(s) links can be fetched: {url}")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise FetchError(f"The link's port is not a number: {url}") from e
    if port not in policy.ports:
        raise FetchError(f"Only links on the standard web ports can be fetched: {url}")
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError(f"Can't find the host {parts.hostname}") from e
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses or not all(policy.allow(address) for address in addresses):
        raise FetchError(f"{parts.hostname} is not a public address")
    return parts.hostname, port, addresses[0]


def _pinned_url(url: str, address: IPAddress) -> str:
    """Returns the URL with its host replaced by a resolved address."""
    parts = urlsplit(url)
    host = f"[{address}]" if address.version == 6 else str(address)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return parts._replace(netloc=netloc).geturl()


def _host_header(hostname: str, url: str) -> str:
    """The Host header for a URL, its name in ASCII ('bücher.de' -> IDNA)."""
    port = urlsplit(url).port
    name = hostname.encode("idna").decode("ascii")
    return f"{name}:{port}" if port else name


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
def _get(
    url: str, hostname: str, address: IPAddress, policy: FetchPolicy
) -> Iterator[requests.Response]:
    """Streams a GET of the URL, connected to the given address.

    Proxies from the environment are ignored: a proxy would resolve the host
    again itself.
    """
    with requests.Session() as session:
        session.trust_env = False
        session.mount("https://", _PinnedTLSAdapter(hostname))
        with session.get(
            _pinned_url(url, address),
            headers={"Host": _host_header(hostname, url), "User-Agent": USER_AGENT},
            timeout=(policy.connect_timeout_s, policy.read_timeout_s),
            stream=True,
            allow_redirects=False,
            verify=policy.verify,
        ) as response:
            yield response


def _read_body(
    response: requests.Response, policy: FetchPolicy, deadline: float
) -> bytes:
    """Reads a response body within the size cap and the deadline.

    `read1` returns whatever has arrived, so a server trickling a byte at a
    time meets the deadline instead of holding a read open. Bytes are counted
    unpacked: a small gzip body can't expand past the cap.
    """
    too_big = FetchError(f"The document is over {policy.max_bytes / 2**20:g} MB")
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > policy.max_bytes:
        raise too_big
    body = bytearray()
    while chunk := response.raw.read1(_CHUNK_BYTES, decode_content=True):
        body += chunk
        if len(body) > policy.max_bytes:
            raise too_big
        if time.monotonic() > deadline:
            raise FetchError("The site took too long to send the document.")
    return bytes(body)


def _status_error(response: requests.Response) -> FetchError:
    """The builder-facing error for a response that isn't the document."""
    if response.status_code in (401, 403):
        return FetchError(
            f"The site refused the download ({response.status_code}).",
            "The link may need a sign-in: download the file and upload it instead.",
        )
    if response.status_code == 404:
        return FetchError("Nothing was found at that link (404).")
    return FetchError(
        f"The site answered {response.status_code} {response.reason or ''}".strip()
        + " instead of the document."
    )


def fetch_url(url: str, policy: FetchPolicy | None = None) -> FetchedDocument:
    """Downloads a public http(s) URL, following redirects that stay public.

    Args:
        url: The URL as typed; share links are rewritten to their file.
        policy: Limits and reach; `DEFAULT_POLICY` when not given.

    Returns:
        The downloaded document.

    Raises:
        FetchError: The URL or a redirect leaves what the policy allows, a
            redirect drops from https to http, there are too many redirects,
            the body is too big or too slow, the request fails, or the server
            answers with an error. The message is meant for the builder.
    """
    policy = policy or DEFAULT_POLICY
    deadline = time.monotonic() + policy.deadline_s
    url = direct_link(url.strip())
    for _ in range(policy.max_redirects + 1):
        hostname, _port, address = _target(url, policy)
        try:
            with _get(url, hostname, address, policy) as response:
                if response.is_redirect:
                    nxt = urljoin(url, response.headers["location"])
                    if (
                        urlsplit(url).scheme == "https"
                        and urlsplit(nxt).scheme == "http"
                    ):
                        raise FetchError(
                            "The link redirects from a secure (https) address to "
                            "an insecure one."
                        )
                    url = nxt
                    continue
                if not response.ok:
                    raise _status_error(response)
                content_type = response.headers.get("content-type", "")
                return FetchedDocument(
                    _read_body(response, policy, deadline),
                    content_type.split(";")[0].strip().lower(),
                    url,
                )
        except requests.exceptions.SSLError as e:
            raise FetchError(
                f"{hostname}'s security certificate couldn't be verified."
            ) from e
        except (requests.exceptions.Timeout, urllib3.exceptions.TimeoutError) as e:
            raise FetchError(f"{hostname} took too long to answer.") from e
        except (
            requests.exceptions.RequestException,
            urllib3.exceptions.HTTPError,
        ) as e:
            raise FetchError(f"Couldn't download from {hostname}.") from e
    raise FetchError(f"Too many redirects from {url}")
