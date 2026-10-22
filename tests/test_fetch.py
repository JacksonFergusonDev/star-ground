"""Tests for fetching a BOM from a user's URL."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from src.bom_lib import fetch
from src.bom_lib.fetch import FetchError, fetch_url


def _resolve(address: str):
    return lambda host, port: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
    ]


def _response(*, body: bytes = b"", location: str | None = None) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.is_redirect = location is not None
    response.headers = {"location": location} if location else {}
    response.iter_content.return_value = [body]
    return response


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/bom.pdf", "gopher://example.com/"],
)
def test_only_http_links_are_fetched(url: str) -> None:
    with patch("requests.get") as get, pytest.raises(FetchError, match="http"):
        fetch_url(url)
    get.assert_not_called()


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"]
)
def test_the_servers_own_network_is_refused(address: str) -> None:
    with (
        patch("socket.getaddrinfo", _resolve(address)),
        patch("requests.get") as get,
        pytest.raises(FetchError, match="not a public address"),
    ):
        fetch_url("http://metadata.example/latest")
    get.assert_not_called()


def test_a_redirect_into_the_servers_network_is_refused() -> None:
    def resolve(host, port):
        return _resolve("10.0.0.1" if host == "internal" else "93.184.216.34")(
            host, port
        )

    with (
        patch("socket.getaddrinfo", resolve),
        patch(
            "requests.get", return_value=_response(location="http://internal/x")
        ) as get,
        pytest.raises(FetchError, match="not a public address"),
    ):
        fetch_url("https://example.com/bom.pdf")
    assert get.call_count == 1


def test_public_redirects_are_followed() -> None:
    responses = [_response(location="/files/bom.pdf"), _response(body=b"%PDF-1.4")]
    with (
        patch("socket.getaddrinfo", _resolve("93.184.216.34")),
        patch("requests.get", side_effect=responses) as get,
    ):
        assert fetch_url("https://example.com/bom") == b"%PDF-1.4"
    assert get.call_args.args[0] == "https://example.com/files/bom.pdf"
    assert get.call_args.kwargs["allow_redirects"] is False


def test_an_oversized_document_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "MAX_BYTES", 4)
    with (
        patch("socket.getaddrinfo", _resolve("93.184.216.34")),
        patch("requests.get", return_value=_response(body=b"%PDF-1.4")),
        pytest.raises(FetchError, match="over"),
    ):
        fetch_url("https://example.com/huge.pdf")
