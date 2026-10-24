"""URL fetching over real sockets, against local HTTP and HTTPS servers.

Nothing here is mocked below `socket.getaddrinfo`: requests, urllib3, TLS,
redirects, streaming and the address pinning all run for real. Names ending
'.test' resolve to the servers on 127.0.0.1 (or wherever a test says), and the
fetch policy admits loopback and the servers' ports, with a test CA's
certificates trusted. No request leaves the machine.
"""

import gzip
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import trustme

from src.bom_lib import ComponentCategory, ComponentKey, InputMethod, fetch
from src.bom_lib.fetch import FetchError, FetchPolicy, fetch_url
from src.bom_lib.strategies.context import BOMParserContext

SAMPLE_PDF = Path(__file__).parent / "samples" / "clean" / "Cataclysm-PedalPCB.pdf"
LIMIT = 256 * 1024  # for the size tests; documents get the default cap


@dataclass
class Site:
    """The local servers and what they saw."""

    http_port: int
    https_port: int
    ca_path: str
    requests: list[dict[str, str]] = field(default_factory=list)

    def url(self, path: str, host: str = "docs.test", tls: bool = False) -> str:
        scheme, port = ("https", self.https_port) if tls else ("http", self.http_port)
        return f"{scheme}://{host}:{port}{path}"

    def policy(self, **overrides: object) -> FetchPolicy:
        base = FetchPolicy(
            ports=frozenset({self.http_port, self.https_port}),
            allow=lambda address: address.is_loopback,
            verify=self.ca_path,
            deadline_s=5.0,
            read_timeout_s=2.0,
        )
        return replace(base, **overrides)  # type: ignore[arg-type]


def _handler(site: Site) -> type[BaseHTTPRequestHandler]:
    pdf = SAMPLE_PDF.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            pass

        def send(self, status: int, body: bytes = b"", **headers: str) -> None:
            self.send_response(status)
            for name, value in {"Content-Length": str(len(body)), **headers}.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, location: str) -> None:
            self.send(302, Location=location)

        def do_GET(self) -> None:
            site.requests.append(
                {
                    "path": self.path,
                    "host": self.headers["Host"],
                    "ua": self.headers["User-Agent"],
                }
            )
            routes: dict[str, Callable[[], None]] = {
                "/bom.pdf": lambda: self.send(200, pdf, Content_Type="application/pdf"),
                "/bom.csv": lambda: self.send(
                    200,
                    b"Designator,Value,Qty\nR1,10k,1\nC1,100n,1\n",
                    Content_Type="text/csv",
                ),
                "/bom.txt": lambda: self.send(
                    200, b"R1 10k\nC1 100n\n", Content_Type="text/plain"
                ),
                "/page": lambda: self.send(
                    200,
                    b"<!DOCTYPE html><p>download warning</p>",
                    Content_Type="text/html",
                ),
                "/broken.pdf": lambda: self.send(
                    200, b"\x00\x01 not a pdf", Content_Type="application/pdf"
                ),
                "/missing": lambda: self.send(404, b"not here"),
                "/private": lambda: self.send(403, b"sign in"),
                "/redirect": lambda: self.redirect("/bom.pdf"),
                "/loop": lambda: self.redirect("/loop"),
                "/to-private": lambda: self.redirect(
                    f"http://private.test:{site.http_port}/bom.pdf"
                ),
                "/downgrade": lambda: self.redirect(
                    f"http://docs.test:{site.http_port}/bom.pdf"
                ),
                "/downgrade-upper": lambda: self.redirect(
                    f"HTTP://docs.test:{site.http_port}/bom.pdf"
                ),
                "/declared-big": lambda: self.big_declared(),
                "/streamed-big": lambda: self.streamed_big(),
                "/gzip-bomb": lambda: self.gzip_bomb(),
                "/slow": lambda: self.slow(),
                "/stall": lambda: self.stall(),
            }
            routes.get(self.path, lambda: self.send(404))()

        def big_declared(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(LIMIT * 10))
            self.end_headers()
            self.wfile.write(b"x" * 1024)

        def streamed_big(self) -> None:
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk = b"x" * 64 * 1024
            try:
                for _ in range(64):
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass  # the client stopped reading at its limit

        def gzip_bomb(self) -> None:
            body = gzip.compress(b"\x00" * LIMIT * 40)  # 10 MB unpacked, ~10 kB sent
            self.send(200, body, Content_Encoding="gzip", Content_Type="text/plain")

        def slow(self) -> None:
            body = b"R1 10k\n" * 40
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

        def stall(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            time.sleep(3)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # a client hanging up at its limit is the point of several tests


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    return thread


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    ca = trustme.CA()
    cert = ca.issue_cert("docs.test")
    ca_path = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_path))

    site = Site(0, 0, str(ca_path))
    http = _Server(("127.0.0.1", 0), _handler(site))
    https = _Server(("127.0.0.1", 0), _handler(site))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    cert.configure_cert(context)
    https.socket = context.wrap_socket(https.socket, server_side=True)
    site.http_port, site.https_port = http.server_address[1], https.server_address[1]

    real = socket.getaddrinfo
    hosts = {
        "docs.test": "127.0.0.1",
        "other.test": "127.0.0.1",
        "private.test": "10.0.0.1",
    }

    def resolve(host, port, *args, **kwargs):
        if host in hosts:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], port))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    servers = [http, https]
    for server in servers:
        _serve(server)
    yield site
    for server in servers:
        server.shutdown()
        server.server_close()


class TestDocuments:
    def test_a_pdf_is_downloaded(self, site: Site) -> None:
        doc = fetch_url(site.url("/bom.pdf"), site.policy())
        assert doc.content == SAMPLE_PDF.read_bytes()
        assert doc.content_type == "application/pdf"

    def test_the_host_and_user_agent_are_sent(self, site: Site) -> None:
        fetch_url(site.url("/bom.txt"), site.policy())
        [seen] = site.requests
        assert seen["host"] == f"docs.test:{site.http_port}"
        assert seen["ua"] == fetch.USER_AGENT

    def test_a_redirect_is_followed_and_reported(self, site: Site) -> None:
        doc = fetch_url(site.url("/redirect"), site.policy())
        assert doc.content.startswith(b"%PDF")
        assert doc.url == site.url("/bom.pdf")
        assert [r["path"] for r in site.requests] == ["/redirect", "/bom.pdf"]


class TestPinning:
    def test_the_checked_address_is_the_one_connected_to(
        self, site: Site, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A host that resolves publicly for the check and privately after it
        (DNS rebinding) is connected to at the checked address."""
        answers = iter(["127.0.0.1", "10.0.0.1"])
        real = socket.getaddrinfo

        def rebinding(host, port, *args, **kwargs):
            if host == "rebind.test":
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))
                ]
            return real(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", rebinding)
        doc = fetch_url(site.url("/bom.txt", host="rebind.test"), site.policy())
        assert doc.content == b"R1 10k\nC1 100n\n"
        assert next(answers) == "10.0.0.1"  # resolved once: the second answer is unused

    def test_a_redirect_into_a_private_network_is_refused(self, site: Site) -> None:
        with pytest.raises(FetchError, match="not a public address"):
            fetch_url(site.url("/to-private"), site.policy())
        assert [r["path"] for r in site.requests] == ["/to-private"]

    def test_loopback_is_refused_by_the_default_rule(self, site: Site) -> None:
        with pytest.raises(FetchError, match="not a public address"):
            fetch_url(site.url("/bom.pdf"), site.policy(allow=fetch.is_public))
        assert site.requests == []


class TestTLS:
    def test_https_is_verified_against_the_hostname(self, site: Site) -> None:
        doc = fetch_url(site.url("/bom.pdf", tls=True), site.policy())
        assert doc.content.startswith(b"%PDF")

    def test_a_certificate_for_another_name_is_refused(self, site: Site) -> None:
        """'other.test' resolves to the same server, whose certificate names
        docs.test: connecting by address must not skip the name check."""
        with pytest.raises(FetchError, match="certificate"):
            fetch_url(site.url("/bom.pdf", host="other.test", tls=True), site.policy())

    def test_an_untrusted_certificate_is_refused(self, site: Site) -> None:
        with pytest.raises(FetchError, match="certificate"):
            fetch_url(site.url("/bom.pdf", tls=True), site.policy(verify=True))

    @pytest.mark.parametrize("path", ["/downgrade", "/downgrade-upper"])
    def test_a_redirect_from_https_to_http_is_refused(
        self, site: Site, path: str
    ) -> None:
        with pytest.raises(FetchError, match="insecure"):
            fetch_url(site.url(path, tls=True), site.policy())


class TestLimits:
    def test_too_many_redirects(self, site: Site) -> None:
        with pytest.raises(FetchError, match="Too many redirects"):
            fetch_url(site.url("/loop"), site.policy(max_redirects=3))
        assert len(site.requests) == 4

    def test_a_declared_size_over_the_limit_is_refused_before_reading(
        self, site: Site
    ) -> None:
        with pytest.raises(FetchError, match="over"):
            fetch_url(site.url("/declared-big"), site.policy(max_bytes=LIMIT))

    def test_a_stream_over_the_limit_is_cut_off(self, site: Site) -> None:
        with pytest.raises(FetchError, match="over"):
            fetch_url(site.url("/streamed-big"), site.policy(max_bytes=LIMIT))

    def test_the_limit_counts_unpacked_bytes(self, site: Site) -> None:
        """A small gzip body that unpacks to megabytes is refused."""
        with pytest.raises(FetchError, match="over"):
            fetch_url(site.url("/gzip-bomb"), site.policy(max_bytes=LIMIT))

    def test_a_trickling_server_runs_into_the_deadline(self, site: Site) -> None:
        start = time.monotonic()
        with pytest.raises(FetchError, match="too long"):
            fetch_url(site.url("/slow"), site.policy(deadline_s=0.5))
        assert time.monotonic() - start < 2.0

    def test_a_stalled_server_times_out(self, site: Site) -> None:
        with pytest.raises(FetchError, match="too long"):
            fetch_url(site.url("/stall"), site.policy(read_timeout_s=0.3))


class TestErrors:
    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("/missing", "Nothing was found at that link"),
            ("/private", "refused the download"),
        ],
    )
    def test_an_error_status_is_explained(
        self, site: Site, path: str, message: str
    ) -> None:
        with pytest.raises(FetchError, match=message):
            fetch_url(site.url(path), site.policy())

    def test_a_closed_port_is_explained(self, site: Site) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        policy = site.policy(ports=frozenset({closed}))
        with pytest.raises(FetchError, match=r"Couldn't download from docs\.test"):
            fetch_url(f"http://docs.test:{closed}/bom.pdf", policy)


class TestParsing:
    """The whole 'From URL' path: fetch, decide what the document is, parse it."""

    @pytest.fixture(autouse=True)
    def _policy(self, site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fetch, "DEFAULT_POLICY", site.policy())

    def _process(self, url: str):
        return BOMParserContext().process(InputMethod.FROM_URL, url, "Linked")

    def test_a_linked_pdf_is_parsed(self, site: Site) -> None:
        result = self._process(site.url("/bom.pdf"))
        assert result.stats["errors"] == []
        assert result.stats["parts_found"] > 0
        assert result.raw_content == SAMPLE_PDF.read_bytes()

    def test_a_linked_csv_is_read_as_columns(self, site: Site) -> None:
        """A CSV read as pasted text gave a '10k,1' resistor."""
        result = self._process(site.url("/bom.csv"))
        assert result.stats["errors"] == []
        assert set(result.inventory) == {
            ComponentKey(ComponentCategory.RESISTORS, "10k"),
            ComponentKey(ComponentCategory.CAPACITORS, "100n"),
        }

    def test_linked_text_is_read_as_pasted_text(self, site: Site) -> None:
        result = self._process(site.url("/bom.txt"))
        assert result.stats["parts_found"] == 2

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("/page", "opened a web page"),
            ("/broken.pdf", "damaged PDF"),
            ("/missing", "404"),
        ],
    )
    def test_a_link_that_is_no_document_is_explained(
        self, site: Site, path: str, message: str
    ) -> None:
        result = self._process(site.url(path))
        assert result.stats["parts_found"] == 0
        assert message in result.stats["errors"][0]
