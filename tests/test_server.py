"""Tests for the HTTP API (`src.server`), through FastAPI's test client."""

import ast
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from src.api import read_document
from src.bom_lib import (
    BOM_PRESETS,
    FeedbackRating,
    FileUpload,
    InputMethod,
    ParseResult,
)
from src.server import Settings, create_app
from src.server.limits import RateLimiter, ReadGate, ReadingCache
from tools.export_openapi import OPENAPI_FILE, schema_text

ROOT = Path(__file__).parent.parent
SAMPLES = Path(__file__).parent / "samples"
KLICHE = SAMPLES / "clean" / "Kliche-PedalPCB.pdf"
UNGULA = SAMPLES / "dirty" / "Ungula.pdf"
PRESET = next(iter(BOM_PRESETS))

type Files = list[tuple[str, tuple[str, bytes, str]]]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(requests_per_minute=1000)))


def _pdf(path: Path) -> tuple[str, bytes, str]:
    return (path.name, path.read_bytes(), "application/pdf")


def _events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _post(
    client: TestClient,
    route: str,
    projects: list[dict[str, Any]],
    files: Files | None = None,
    **options: Any,
) -> Any:
    request = json.dumps({"projects": projects, **options})
    return client.post(route, data={"request": request}, files=files or [])


# --- Service routes ---


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_presets_list_the_library(client: TestClient) -> None:
    catalog = client.get("/api/presets").json()

    assert {p["key"] for p in catalog["presets"]} == set(BOM_PRESETS)
    assert "PedalPCB" in catalog["sources"]
    assert all(catalog["categories"][s] for s in catalog["sources"])


# --- Reading documents ahead of time ---


def test_reading_an_upload_streams_its_reading(client: TestClient) -> None:
    response = client.post("/api/documents/read", files={"file": _pdf(KLICHE)})

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [e["event"] for e in events] == ["reading", "result"]
    reading = events[-1]["reading"]
    assert reading["title"] == "Kliche Overdrive"
    assert reading["file_title"] == "Kliche-PedalPCB"
    assert reading["parts_found"] > 0
    assert reading["builds"] == []
    assert reading["ocr"] is None


def test_a_document_read_again_comes_from_the_cache(
    client: TestClient, mocker: MockerFixture
) -> None:
    read = mocker.patch("src.server.app.read_document", wraps=read_document)
    client.post("/api/documents/read", files={"file": _pdf(KLICHE)})

    again = client.post("/api/documents/read", files={"file": _pdf(KLICHE)})

    assert read.call_count == 1
    assert [e["event"] for e in _events(again.text)] == ["result"]


def test_a_multi_circuit_document_lists_its_builds(client: TestClient) -> None:
    response = client.post("/api/documents/read", files={"file": _pdf(UNGULA)})

    assert _events(response.text)[-1]["reading"]["builds"] == ["Stock", "Cleft Mod"]


def test_a_link_is_read_as_a_link(client: TestClient, mocker: MockerFixture) -> None:
    upload = FileUpload("Kliche-PedalPCB.pdf", KLICHE.read_bytes())
    real = read_document(InputMethod.UPLOAD_FILE, upload)
    read = mocker.patch("src.server.app.read_document", return_value=real)
    url = "https://docs.pedalpcb.com/project/Kliche-PedalPCB.pdf"

    response = client.post("/api/documents/read", data={"url": f" {url} "})

    read.assert_called_once_with(InputMethod.FROM_URL, url)
    assert _events(response.text)[-1]["reading"]["file_title"] == "Kliche-PedalPCB"


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {
            "files": {"file": ("a.pdf", b"%PDF", "application/pdf")},
            "data": {"url": "x"},
        },
    ],
    ids=["neither", "both"],
)
def test_a_read_takes_a_file_or_a_link(
    client: TestClient, fields: dict[str, Any]
) -> None:
    response = client.post("/api/documents/read", **fields)

    assert response.status_code == 422
    assert response.json() == {"message": "Send either a file or a url."}


def test_a_failed_read_is_an_error_event(
    client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch("src.server.app.read_document", side_effect=RuntimeError("boom"))

    response = client.post("/api/documents/read", files={"file": _pdf(KLICHE)})

    events = _events(response.text)
    assert events[-1]["event"] == "error"
    assert "boom" not in events[-1]["message"]


# --- Master list ---


def test_a_master_list_merges_every_kind_of_project(client: TestClient) -> None:
    response = _post(
        client,
        "/api/master-list",
        [
            {"method": "upload", "file": KLICHE.name, "count": 2},
            {"method": "preset", "preset": PRESET},
            {"method": "paste", "text": "R1 10k\nC1 100n", "name": "Mine"},
        ],
        files=[("files", _pdf(KLICHE))],
    )

    assert response.status_code == 200
    result = response.json()
    preset_name = PRESET.rsplit("] ", 1)[-1]
    assert [p["label"] for p in result["projects"]] == [
        "Kliche Overdrive",
        preset_name,
        "Mine",
    ]
    assert result["stats"]["parts_found"] > 0
    assert result["has_stock"] is False
    tens = next(r for r in result["shopping_list"] if r["part"] == "10k")
    assert tens["bom_qty"] >= 1
    assert tens["supplier_url"].startswith("https://")


def test_stock_is_subtracted(client: TestClient) -> None:
    request = json.dumps({"projects": [{"method": "paste", "text": "R1 10k"}]})
    stock = b"Category,Part,Qty\nResistors,10k,1\n"

    response = client.post(
        "/api/master-list",
        data={"request": request},
        files={"stock": ("stock.csv", stock, "text/csv")},
    )

    result = response.json()
    assert result["has_stock"] is True
    row = next(r for r in result["shopping_list"] if r["part"] == "10k")
    assert (row["bom_qty"], row["in_stock"], row["net_need"]) == (1, 1, 0)


def test_a_multi_circuit_document_needs_its_build_chosen(client: TestClient) -> None:
    project = {"method": "upload", "file": UNGULA.name}
    files: Files = [("files", _pdf(UNGULA))]

    unchosen = _post(client, "/api/master-list", [project], files=files)
    chosen = _post(
        client, "/api/master-list", [{**project, "variant": "Stock"}], files=files
    )

    assert unchosen.status_code == 422
    assert unchosen.json()["projects"] == ["Ungula"]
    assert chosen.status_code == 200
    assert chosen.json()["projects"][0]["label"] == "Ungula (Stock)"


def test_warnings_are_grouped_by_part(client: TestClient) -> None:
    response = _post(
        client,
        "/api/master-list",
        [{"method": "upload", "file": UNGULA.name, "variant": "Stock"}],
        files=[("files", _pdf(UNGULA))],
    )

    for review in response.json()["projects"][0]["reviews"]:
        assert review["warnings"]
        assert all(w["ref"] == review["ref"] for w in review["warnings"])


@pytest.mark.parametrize(
    ("projects", "files", "message"),
    [
        ([{"method": "preset", "preset": "[Nope] Nothing"}], [], "no preset"),
        ([{"method": "upload", "file": "missing.pdf"}], [], "No file named"),
        (
            [{"method": "upload", "file": KLICHE.name}],
            [("files", _pdf(KLICHE)), ("files", _pdf(KLICHE))],
            "Two files",
        ),
    ],
    ids=["unknown preset", "missing upload", "duplicate upload"],
)
def test_bad_projects_are_refused(
    client: TestClient, projects: list[dict[str, Any]], files: Files, message: str
) -> None:
    response = _post(client, "/api/master-list", projects, files=files)

    assert response.status_code == 422
    assert message in response.json()["message"]


@pytest.mark.parametrize(
    "request_body",
    [
        "not json",
        json.dumps({"projects": []}),
        json.dumps({"projects": [{"method": "paste", "text": "R1 10k", "count": 0}]}),
        json.dumps({"projects": [{"method": "fax", "text": "R1 10k"}]}),
    ],
    ids=["not json", "no projects", "no pedals", "unknown method"],
)
def test_invalid_requests_are_refused(client: TestClient, request_body: str) -> None:
    response = client.post("/api/master-list", data={"request": request_body})

    assert response.status_code == 422


# --- Exports ---


@pytest.mark.parametrize(
    ("kind", "media_type"),
    [
        ("shopping-list", "text/csv"),
        ("supplier-cart", "text/csv"),
        ("stock-update", "text/csv"),
        ("field-manuals", "application/zip"),
        ("build-pack", "application/zip"),
    ],
)
def test_each_export_is_an_attachment(
    client: TestClient, kind: str, media_type: str
) -> None:
    response = _post(
        client, f"/api/exports/{kind}", [{"method": "paste", "text": "R1 10k"}]
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    extension = "csv" if media_type == "text/csv" else "zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{kind}.{extension}"'
    )


def test_the_build_pack_keeps_an_uploaded_document(client: TestClient) -> None:
    response = _post(
        client,
        "/api/exports/build-pack",
        [{"method": "upload", "file": KLICHE.name}],
        files=[("files", _pdf(KLICHE))],
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        source = zf.read("Source Documents/Kliche Overdrive Source.pdf")
    assert source == KLICHE.read_bytes()


def test_excel_links_are_hyperlink_formulas(client: TestClient) -> None:
    project = [{"method": "paste", "text": "R1 10k"}]

    def links(fmt: str) -> list[str]:
        response = client.post(
            f"/api/exports/shopping-list?links={fmt}",
            data={"request": json.dumps({"projects": project})},
        )
        rows = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
        return [v for row in rows for v in row.values() if "taydaelectronics" in v]

    excel, plain = links("excel"), links("plain")
    assert excel
    assert all(v.startswith("=HYPERLINK") for v in excel)
    assert plain
    assert all(v.startswith("https://") for v in plain)


# --- Feedback ---


def test_feedback_reaches_the_sink() -> None:
    received: list[tuple[FeedbackRating, str]] = []
    client = TestClient(
        create_app(Settings(), feedback=lambda r, c: received.append((r, c)))
    )

    response = client.post("/api/feedback", json={"rating": 5, "comment": "Great"})

    assert response.status_code == 204
    assert received == [(FeedbackRating.EXCELLENT, "Great")]


def test_feedback_without_a_sink_is_unavailable(client: TestClient) -> None:
    response = client.post("/api/feedback", json={"rating": 3})

    assert response.status_code == 503


@pytest.mark.parametrize("rating", [0, 6])
def test_a_rating_is_one_to_five(client: TestClient, rating: int) -> None:
    assert client.post("/api/feedback", json={"rating": rating}).status_code == 422


# --- Limits ---


def test_clients_are_rate_limited() -> None:
    client = TestClient(create_app(Settings(requests_per_minute=2)))

    statuses = [
        client.post("/api/feedback", json={"rating": 3}).status_code for _ in range(3)
    ]

    assert statuses == [503, 503, 429]


def test_behind_a_proxy_each_forwarded_client_is_limited_separately() -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1, trust_proxy=True)))

    def post(forwarded: str) -> int:
        return client.post(
            "/api/feedback",
            json={"rating": 3},
            headers={"x-forwarded-for": forwarded},
        ).status_code

    assert post("spoofed, 1.1.1.1") == 503
    assert post("other, 1.1.1.1") == 429
    assert post("2.2.2.2") == 503


def test_a_body_over_the_limit_is_refused() -> None:
    client = TestClient(create_app(Settings(max_body_bytes=1_000)))

    response = client.post(
        "/api/documents/read", files={"file": ("big.pdf", b"x" * 5_000, "x")}
    )

    assert response.status_code == 413


def test_cors_allows_only_the_configured_origin() -> None:
    site = "https://star-ground.jacksonferguson.me"
    client = TestClient(create_app(Settings(cors_origins=(site,))))

    allowed = client.get("/api/health", headers={"origin": site})
    other = client.get("/api/health", headers={"origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == site
    assert "access-control-allow-origin" not in other.headers


def test_the_rate_limit_window_slides() -> None:
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])

    assert limiter.allow("a")
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 60.0
    assert limiter.allow("a")


def test_a_full_read_gate_reports_a_queue_position() -> None:
    gate = ReadGate(1)

    assert gate.queue_position() == 0
    with gate.slot():
        assert gate.queue_position() == 1
    assert gate.queue_position() == 0


def _reading(size: int) -> ParseResult:
    return ParseResult(
        inventory=None,  # type: ignore[arg-type]
        stats=None,  # type: ignore[arg-type]
        raw_content=b"x" * size,
    )


def test_the_reading_cache_evicts_the_least_recently_used() -> None:
    cache = ReadingCache(max_bytes=250_000)
    cache.put("a", _reading(100_000))
    cache.put("b", _reading(100_000))
    assert cache.get("a") is not None  # now the most recently used

    cache.put("c", _reading(100_000))

    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None


def test_a_document_too_big_for_the_cache_isnt_cached() -> None:
    cache = ReadingCache(max_bytes=100_000)

    cache.put("a", _reading(200_000))

    assert cache.get("a") is None


# --- Configuration and contract ---


def test_settings_come_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "STAR_GROUND_CORS_ORIGINS": "https://a.example, https://b.example",
            "STAR_GROUND_READ_SLOTS": "1",
            "STAR_GROUND_REQUESTS_PER_MINUTE": "5",
            "STAR_GROUND_TRUST_PROXY": "1",
        }
    )

    assert settings.cors_origins == ("https://a.example", "https://b.example")
    assert (settings.read_slots, settings.requests_per_minute) == (1, 5)
    assert settings.trust_proxy


def test_default_settings_from_an_empty_environment() -> None:
    assert Settings.from_env({}) == Settings()


def test_the_committed_openapi_schema_is_current() -> None:
    """The frontend's types come from `openapi.json`; run `just openapi`."""
    assert OPENAPI_FILE.read_text(encoding="utf-8") == schema_text()


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _backend_modules() -> list[Path]:
    src = ROOT / "src"
    return [p for p in src.rglob("*.py") if "server" not in p.relative_to(src).parts]


@pytest.mark.parametrize(
    "path", _backend_modules(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_the_backend_does_not_depend_on_the_web_layer(path: Path) -> None:
    """The API wraps the backend; the backend never imports the API."""
    forbidden: set[str] = {"fastapi", "starlette", "pydantic", "uvicorn"}
    imports = _imports(path)
    assert not {m for m in imports if m.split(".")[0] in forbidden}
    assert not {m for m in imports if m == "src.server" or m.startswith("src.server.")}
