"""Unit tests for the URL fetch rules: what may be fetched, and how links are rewritten.

Fetching itself, over real sockets, is tested in `test_fetch_server.py`.
"""

import ipaddress
import socket

import pytest

from src.bom_lib import fetch
from src.bom_lib.errors import FetchError
from src.bom_lib.fetch import direct_link, fetch_url, is_public


def _resolve(*addresses: str):
    def resolve(host, port, **kwargs):
        return [
            (
                socket.AF_INET6 if ":" in a else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, port),
            )
            for a in addresses
        ]

    return resolve


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/bom.pdf", "gopher://example.com/"],
)
def test_only_http_links_are_fetched(url: str) -> None:
    with pytest.raises(FetchError, match="http"):
        fetch_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:22/",
        "https://example.com:6379/bom.pdf",
        "http://example.com:8080/",
    ],
)
def test_only_the_standard_web_ports_are_fetched(url: str) -> None:
    """A port scan of a public host through the app is refused."""
    with pytest.raises(FetchError, match="standard web ports"):
        fetch_url(url)


def test_a_malformed_port_is_refused() -> None:
    with pytest.raises(FetchError, match="port"):
        fetch_url("http://example.com:http/")


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1::", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped
        ("64:ff9b::7f00:1", False),  # NAT64 of 127.0.0.1, which Python calls global
        ("64:ff9b::5db8:d822", True),  # NAT64 of 93.184.216.34
    ],
)
def test_public_addresses(address: str, public: bool) -> None:
    assert is_public(ipaddress.ip_address(address)) is public


def test_a_host_with_any_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolve("93.184.216.34", "10.0.0.1"))
    with pytest.raises(FetchError, match="not a public address"):
        fetch_url("https://example.com/bom.pdf")


def test_an_unresolvable_host_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args, **kwargs):
        raise socket.gaierror("nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(FetchError, match=r"Can't find the host nowhere\.example"):
        fetch_url("https://nowhere.example/bom.pdf")


@pytest.mark.parametrize(
    ("url", "address", "expected"),
    [
        ("http://example.com/a?b=1", "93.184.216.34", "http://93.184.216.34/a?b=1"),
        ("https://example.com:443/x", "93.184.216.34", "https://93.184.216.34:443/x"),
        (
            "https://example.com/bom.pdf",
            "2606:2800:220:1::",
            "https://[2606:2800:220:1::]/bom.pdf",
        ),
    ],
)
def test_the_url_is_pinned_to_the_checked_address(
    url: str, address: str, expected: str
) -> None:
    assert fetch._pinned_url(url, ipaddress.ip_address(address)) == expected


def test_the_host_header_is_ascii() -> None:
    assert fetch._host_header("bücher.example", "https://bücher.example/x") == (
        "xn--bcher-kva.example"
    )
    assert (
        fetch._host_header("example.com", "http://example.com:80/x") == "example.com:80"
    )


def test_tls_is_checked_against_the_hostname_not_the_address() -> None:
    adapter = fetch._PinnedTLSAdapter("example.com")
    pool = adapter.poolmanager.connection_from_url("https://93.184.216.34/bom.pdf")
    assert pool.host == "93.184.216.34"
    assert pool.conn_kw["server_hostname"] == "example.com"


@pytest.mark.parametrize(
    ("shared", "direct"),
    [
        (
            "https://github.com/owner/repo/blob/main/boms/rat.csv",
            "https://raw.githubusercontent.com/owner/repo/main/boms/rat.csv",
        ),
        (
            "https://drive.google.com/file/d/1AbC-dEf_9/view?usp=sharing",
            "https://drive.google.com/uc?export=download&id=1AbC-dEf_9",
        ),
        (
            "https://www.dropbox.com/scl/fi/abc/rat.pdf?rlkey=xyz&dl=0",
            "https://www.dropbox.com/scl/fi/abc/rat.pdf?rlkey=xyz&dl=1",
        ),
        (
            "https://docs.pedalpcb.com/project/Rat.pdf",
            "https://docs.pedalpcb.com/project/Rat.pdf",
        ),
        ("https://github.com/owner/repo", "https://github.com/owner/repo"),
    ],
)
def test_share_links_point_at_the_file(shared: str, direct: str) -> None:
    assert direct_link(shared) == direct
