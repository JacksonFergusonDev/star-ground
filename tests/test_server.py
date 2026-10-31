"""Tests for the HTTP API (`src.server`), through FastAPI's test client."""

import ast
import csv
import dataclasses
import io
import json
import re
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from pytest_mock import MockerFixture

from src.api import read_document
from src.bom_lib import (
    BOM_PRESETS,
    FeedbackRating,
    FileUpload,
    InputMethod,
    Location,
    ParseResult,
)
from src.bom_lib.errors import BUG_MESSAGE, ErrorKind, OcrRefusedError
from src.bom_lib.ocr import ocr_guard
from src.feedback import PartReport
from src.server import Settings, create_app
from src.server.limits import OcrQueue, RateLimiter, ReadingCache
from src.server.models import BUG_CODE, ReportIn
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

    read.assert_called_once_with(InputMethod.FROM_URL, url, ocr=False)
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
    assert response.json()["message"] == "Send either a file or a url."


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


def test_each_part_says_where_it_was_read(client: TestClient) -> None:
    response = _post(
        client,
        "/api/master-list",
        [
            {"method": "upload", "file": KLICHE.name},
            {"method": "paste", "text": "R1 10k", "name": "Mine"},
        ],
        files=[("files", _pdf(KLICHE))],
    )

    kliche, pasted = response.json()["projects"]
    r1 = next(p for p in kliche["parts"] if p["ref"] == "R1")
    assert r1["category"] == "Resistors"
    assert r1["location"]["page"] == 1
    assert r1["location"]["x0"] < r1["location"]["x1"]
    assert all(p["location"] for p in kliche["parts"])
    assert pasted["parts"] == [
        {"ref": "R1", "category": "Resistors", "value": "10k", "location": None}
    ]


def test_each_project_gives_its_build_order(client: TestClient) -> None:
    text = "IC1 TL072\nC2 100u\nR1 10k\nC1 100n\nR2 1k"
    response = _post(client, "/api/master-list", [{"method": "paste", "text": text}])

    (project,) = response.json()["projects"]
    assert project["build_order"] == [
        {"stage": "Resistors", "refs": ["R1", "R2"]},
        {"stage": "Small capacitors", "refs": ["C1"]},
        {"stage": "Electrolytic capacitors", "refs": ["C2"]},
        {"stage": "Chips", "refs": ["IC1"]},
    ]


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


def test_the_rate_limit_window_can_be_longer() -> None:
    now = [0.0]
    limiter = RateLimiter(1, window=3600, clock=lambda: now[0])

    assert limiter.allow("a")
    now[0] = 3599.0
    assert not limiter.allow("a")
    now[0] = 3600.0
    assert limiter.allow("a")


def test_a_full_ocr_queue_reports_a_queue_position() -> None:
    gate = OcrQueue(1)

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
            "STAR_GROUND_OCR_PER_HOUR": "4",
            "STAR_GROUND_REQUESTS_PER_MINUTE": "5",
            "STAR_GROUND_TRUST_PROXY": "1",
            "STAR_GROUND_SITE_DIR": "/app/site",
        }
    )

    assert settings.cors_origins == ("https://a.example", "https://b.example")
    assert (settings.ocr_per_hour, settings.requests_per_minute) == (4, 5)
    assert settings.trust_proxy
    assert settings.site_dir == Path("/app/site")


def test_serves_the_built_site_beside_the_api(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("landing")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("workspace")
    (tmp_path / "404.html").write_text("missing")
    client = TestClient(create_app(Settings(site_dir=tmp_path)))

    assert client.get("/").text == "landing"
    assert client.get("/app/").text == "workspace"
    assert client.get("/api/health").json() == {"status": "ok"}
    missing = client.get("/nowhere/")
    assert (missing.status_code, missing.text) == (404, "missing")


def test_serves_only_the_api_without_a_site() -> None:
    assert TestClient(create_app(Settings())).get("/").status_code == 404


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


# --- OCR ---


def _ocr_read(
    started: threading.Event | None = None, release: threading.Event | None = None
) -> Any:
    """A stand-in reader that runs 'OCR' through the server's guard when asked."""

    def read(method: InputMethod, data: Any, ocr: bool = True) -> ParseResult | None:
        if ocr:
            with ocr_guard.get()():
                if started:
                    started.set()
                if release:
                    release.wait(5)
        # The document as sent (Kliche's bytes, renamed), read without OCR.
        return read_document(method, data, ocr=False)

    return read


def _upload(name: str) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, KLICHE.read_bytes() + name.encode(), "application/pdf")}


def _read_events(
    client: TestClient, name: str, ocr: bool = False
) -> list[dict[str, Any]]:
    response = client.post(
        "/api/documents/read",
        files=_upload(name),
        data={"ocr": "true"} if ocr else {},
    )
    return _events(response.text)


def _image_reading() -> ParseResult:
    """A reading whose parts list is an image (blank to the text layer)."""
    pdf = FPDF()
    pdf.add_page()
    upload = FileUpload("image.pdf", bytes(pdf.output()))
    reading = read_document(InputMethod.UPLOAD_FILE, upload, ocr=False)
    assert reading is not None
    return reading


def test_a_document_is_read_without_ocr_unless_asked(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1000)))
    read = mocker.patch("src.server.app.read_document", return_value=_image_reading())

    reading = _read_events(client, "image.pdf")[-1]["reading"]

    assert read.call_args.kwargs == {"ocr": False}
    assert reading["ocr"] is None
    assert reading["ocr_offered"] is True


def test_asking_for_ocr_reads_again_with_it(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1000)))
    read = mocker.patch("src.server.app.read_document", return_value=_image_reading())

    _read_events(client, "image.pdf")
    accepted = _read_events(client, "image.pdf", ocr=True)[-1]["reading"]

    assert [c.kwargs for c in read.call_args_list] == [{"ocr": False}, {"ocr": True}]
    assert accepted["ocr_offered"] is False


def test_a_text_document_offers_no_ocr(client: TestClient) -> None:
    reading = _read_events(client, "text.pdf")[-1]["reading"]

    assert reading["ocr_offered"] is False


def test_the_master_list_runs_ocr_only_for_accepted_projects(
    mocker: MockerFixture,
) -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1000)))
    read = mocker.patch("src.server.app.read_document", side_effect=_ocr_read())

    for name, use_ocr in (("a.pdf", False), ("b.pdf", True)):
        project = {"method": "upload", "file": name, "use_ocr": use_ocr}
        _post(
            client,
            "/api/master-list",
            [project],
            files=[("files", _upload(name)["file"])],
        )

    assert [c.kwargs for c in read.call_args_list] == [{"ocr": False}, {"ocr": True}]


def test_with_ocr_off_nothing_is_offered_or_read(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1000, ocr=False)))
    read = mocker.patch("src.server.app.read_document", return_value=_image_reading())

    reading = _read_events(client, "image.pdf", ocr=True)[-1]["reading"]

    assert read.call_args.kwargs == {"ocr": False}
    assert reading["ocr_offered"] is False


def test_ocr_can_be_switched_off_from_the_environment() -> None:
    assert not Settings.from_env({"STAR_GROUND_OCR": "0"}).ocr
    assert Settings.from_env({}).ocr


def test_each_client_has_an_hourly_ocr_allowance(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(ocr_per_hour=1)))
    mocker.patch("src.server.app.read_document", side_effect=_ocr_read())

    first = _read_events(client, "a.pdf", ocr=True)
    second = _read_events(client, "b.pdf", ocr=True)

    assert first[-1]["event"] == "result"
    assert second[-1]["event"] == "error"
    assert "OCR a limited number of times an hour" in second[-1]["message"]


def test_the_master_list_refuses_ocr_past_the_allowance(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(ocr_per_hour=0)))
    mocker.patch("src.server.app.read_document", side_effect=_ocr_read())

    project = {"method": "upload", "file": "a.pdf", "use_ocr": True}
    response = client.post(
        "/api/master-list",
        data={"request": json.dumps({"projects": [project]})},
        files=[("files", _upload("a.pdf")["file"])],
    )

    assert response.status_code == 429
    assert "OCR" in response.json()["message"]


def test_a_text_read_never_waits_behind_ocr(mocker: MockerFixture) -> None:
    app = create_app(Settings(requests_per_minute=1000))
    started, release = threading.Event(), threading.Event()
    mocker.patch(
        "src.server.app.read_document", side_effect=_ocr_read(started, release)
    )
    waiting = threading.Thread(
        target=lambda: _read_events(TestClient(app), "ocr.pdf", ocr=True)
    )
    waiting.start()
    try:
        assert started.wait(5)
        assert _read_events(TestClient(app), "text.pdf")[-1]["event"] == "result"
    finally:
        release.set()
        waiting.join(5)


def test_a_document_waiting_for_ocr_streams_its_place(mocker: MockerFixture) -> None:
    app = create_app(Settings(requests_per_minute=1000))
    started, release = threading.Event(), threading.Event()
    first, second = _ocr_read(started, release), _ocr_read()

    def read(method: InputMethod, data: Any, ocr: bool = True) -> ParseResult | None:
        return (first if data.name == "first.pdf" else second)(method, data, ocr)

    mocker.patch("src.server.app.read_document", side_effect=read)
    holder = threading.Thread(
        target=lambda: _read_events(TestClient(app), "first.pdf", ocr=True)
    )
    holder.start()
    try:
        assert started.wait(5)
        threading.Timer(0.5, release.set).start()
        events = _read_events(TestClient(app), "second.pdf", ocr=True)
    finally:
        release.set()
        holder.join(5)

    assert [e["event"] for e in events] == ["reading", "queued", "reading", "result"]
    assert events[1]["position"] == 1


# --- Document references ---


def test_a_read_upload_can_be_named_instead_of_sent(client: TestClient) -> None:
    read = client.post("/api/documents/read", files={"file": _pdf(KLICHE)})
    document = _events(read.text)[-1]["reading"]["document"]

    sent = _post(
        client,
        "/api/master-list",
        [{"method": "upload", "file": KLICHE.name}],
        files=[("files", _pdf(KLICHE))],
    )
    named = _post(
        client, "/api/master-list", [{"method": "upload", "document": document}]
    )

    assert document.startswith(f"file:{KLICHE.name}:")
    assert named.status_code == 200
    assert named.json() == sent.json()


def test_a_link_reading_has_no_document(
    client: TestClient, mocker: MockerFixture
) -> None:
    upload = FileUpload(KLICHE.name, KLICHE.read_bytes())
    mocker.patch(
        "src.server.app.read_document",
        return_value=read_document(InputMethod.UPLOAD_FILE, upload),
    )

    response = client.post(
        "/api/documents/read", data={"url": "https://example.com/a.pdf"}
    )

    assert _events(response.text)[-1]["reading"]["document"] is None


def test_a_document_this_server_lacks_asks_for_the_file(client: TestClient) -> None:
    gone = f"file:{KLICHE.name}:0123456789abcdef"

    response = client.post(
        "/api/master-list",
        data={
            "request": json.dumps(
                {"projects": [{"method": "upload", "document": gone}]}
            )
        },
    )

    assert response.status_code == 409
    assert response.json()["documents"] == [gone]


def test_a_named_document_can_still_accept_ocr(mocker: MockerFixture) -> None:
    client = TestClient(create_app(Settings(requests_per_minute=1000)))
    read = mocker.patch("src.server.app.read_document", side_effect=_ocr_read())
    document = _read_events(client, "a.pdf")[-1]["reading"]["document"]

    project = {"method": "upload", "document": document, "use_ocr": True}
    _post(client, "/api/master-list", [project])

    assert [c.kwargs for c in read.call_args_list] == [{"ocr": False}, {"ocr": True}]
    assert read.call_args.args[1].name == "a.pdf"


def test_an_upload_names_a_file_or_a_document(client: TestClient) -> None:
    response = client.post(
        "/api/master-list",
        data={"request": json.dumps({"projects": [{"method": "upload"}]})},
    )

    assert response.status_code == 422


# --- Bugs and reports ---


def test_a_bug_in_a_reading_gives_the_builder_only_its_code(
    client: TestClient, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch(
        "src.bom_lib.strategies.pdf.parse_pedalpcb_pdf", side_effect=KeyError("oops")
    )

    reading = _events(
        client.post("/api/documents/read", files={"file": _pdf(KLICHE)}).text
    )[-1]["reading"]

    (error,) = reading["errors"]
    assert error["kind"] == ErrorKind.BUG
    assert error["message"] == BUG_MESSAGE
    assert re.fullmatch(BUG_CODE, error["bug_code"])
    # The traceback is in the log, never the response.
    assert "KeyError" not in json.dumps(reading)
    assert f"Bug {error['bug_code']}" in caplog.text


def test_a_failed_read_is_a_bug_with_a_report(
    client: TestClient, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("src.server.app.read_document", side_effect=RuntimeError("boom"))

    event = _events(
        client.post("/api/documents/read", files={"file": _pdf(KLICHE)}).text
    )[-1]

    assert event["event"] == "error"
    assert event["kind"] == ErrorKind.BUG
    assert event["message"] == BUG_MESSAGE
    assert "boom" not in json.dumps(event)
    assert f"Bug {event['bug_code']}" in caplog.text


def test_a_refused_ocr_read_is_explained_not_a_bug(
    client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch("src.server.app.read_document", side_effect=OcrRefusedError)

    event = _events(
        client.post("/api/documents/read", files={"file": _pdf(KLICHE)}).text
    )[-1]

    assert event["kind"] == ErrorKind.OCR_REFUSED
    assert event["hint"] == "Try again later."
    assert event["bug_code"] is None


def test_each_project_says_why_it_couldnt_be_read(
    client: TestClient, mocker: MockerFixture
) -> None:
    mocker.patch(
        "src.bom_lib.strategies.manual.parse_with_verification",
        side_effect=TypeError("broken"),
    )

    result = _post(
        client,
        "/api/master-list",
        [{"method": "paste", "name": "Fuzz", "text": "R1 10k"}],
    ).json()

    (project,) = result["projects"]
    (error,) = project["errors"]
    assert error["kind"] == ErrorKind.BUG
    assert error["bug_code"]


def test_a_bug_outside_a_reading_is_answered_with_a_code_not_a_bare_500(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("src.server.app.generate_master_list", side_effect=ZeroDivisionError)
    client = TestClient(create_app(Settings(cors_origins=("https://example.com",))))

    request = json.dumps({"projects": [{"method": "paste", "text": "R1 10k"}]})
    response = client.post(
        "/api/master-list",
        data={"request": request},
        headers={"origin": "https://example.com"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["kind"] == ErrorKind.BUG
    assert f"Bug {body['bug_code']}" in caplog.text
    # The browser can read it: the answer passed through CORS.
    assert response.headers["access-control-allow-origin"] == "https://example.com"


_REPORT = {
    "code": "A1B2C3",
    "document": "Blender.pdf",
    "comment": "Added three documents at once.",
}


def test_a_report_gets_the_traceback_this_server_holds(
    mocker: MockerFixture,
) -> None:
    received: list[tuple[Any, str]] = []
    client = TestClient(
        create_app(Settings(), reports=lambda r, d: received.append((r, d)))
    )
    mocker.patch("src.server.app.generate_master_list", side_effect=ZeroDivisionError)
    code = _post(
        client, "/api/master-list", [{"method": "paste", "text": "R1 10k"}]
    ).json()["bug_code"]

    response = client.post("/api/reports", json={**_REPORT, "code": code})

    assert response.status_code == 204
    report, detail = received[0]
    assert report.code == code
    assert "ZeroDivisionError" in detail


def test_a_report_of_a_bug_this_server_doesnt_hold_still_goes_in() -> None:
    received: list[tuple[Any, str]] = []
    client = TestClient(
        create_app(Settings(), reports=lambda r, d: received.append((r, d)))
    )

    response = client.post("/api/reports", json=_REPORT)

    assert response.status_code == 204
    assert received == [(ReportIn.model_validate(_REPORT), "")]


@pytest.mark.parametrize("code", ["a1b2c3", "A1B2C3D", "<script>"])
def test_a_report_needs_a_bug_code(client: TestClient, code: str) -> None:
    response = client.post("/api/reports", json={**_REPORT, "code": code})

    assert response.status_code == 422


def test_reports_answer_503_when_not_set_up(client: TestClient) -> None:
    response = client.post("/api/reports", json=_REPORT)

    assert response.status_code == 503
    assert "aren't set up" in response.json()["message"]


# --- Document review ---


def _kliche_parts(client: TestClient, **project: Any) -> list[dict[str, Any]]:
    response = _post(
        client,
        "/api/master-list",
        [{"method": "upload", "file": KLICHE.name, **project}],
        files=[("files", _pdf(KLICHE))],
    )
    assert response.status_code == 200
    parts: list[dict[str, Any]] = response.json()["projects"][0]["parts"]
    return parts


def test_a_correction_changes_what_the_list_buys(client: TestClient) -> None:
    read = next(p for p in _kliche_parts(client) if p["ref"] == "R1")
    correction = {"ref": "R1", "category": "Resistors", "value": "4.7M"}

    parts = _kliche_parts(client, corrections=[correction])

    r1 = next(p for p in parts if p["ref"] == "R1")
    assert r1["value"] == "4.7M"
    # Still where the document prints it.
    assert r1["location"] == read["location"]


def test_a_correction_moves_only_its_own_part(client: TestClient) -> None:
    before = _kliche_parts(client)
    correction = {"ref": "R1", "category": "Resistors", "value": "4.7M"}

    after = _kliche_parts(client, corrections=[correction])

    assert [p for p in after if p["ref"] != "R1"] == [
        p for p in before if p["ref"] != "R1"
    ]


def test_a_typed_value_is_read_as_the_parser_reads_it(client: TestClient) -> None:
    response = client.post("/api/parts/read", json={"ref": "R1", "value": "2K2"})

    assert response.json() == {"category": "Resistors", "value": "2.2k"}


def test_a_value_that_isnt_a_part_is_explained(client: TestClient) -> None:
    response = client.post("/api/parts/read", json={"ref": "R1", "value": " "})

    assert response.status_code == 422
    assert response.json()["kind"] == ErrorKind.UNREADABLE_PART


def test_a_linked_documents_pdf_comes_from_its_reading(
    client: TestClient, mocker: MockerFixture
) -> None:
    upload = FileUpload("Kliche-PedalPCB.pdf", KLICHE.read_bytes())
    real = read_document(InputMethod.UPLOAD_FILE, upload)
    read = mocker.patch("src.server.app.read_document", return_value=real)
    url = "https://docs.pedalpcb.com/project/Kliche-PedalPCB.pdf"
    client.post("/api/documents/read", data={"url": url})

    response = client.get("/api/documents/pdf", params={"url": url})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == KLICHE.read_bytes()
    read.assert_called_once()


def test_a_link_that_isnt_a_pdf_has_nothing_to_show(
    client: TestClient, mocker: MockerFixture
) -> None:
    real = read_document(
        InputMethod.UPLOAD_FILE, FileUpload("k.pdf", KLICHE.read_bytes())
    )
    assert real is not None
    mocker.patch(
        "src.server.app.read_document",
        return_value=dataclasses.replace(real, raw_content=b"Ref,Value"),
    )

    response = client.get("/api/documents/pdf", params={"url": "https://x.com/a.csv"})

    assert response.status_code == 404


_PART_REPORT = {
    "document": "https://docs.pedalpcb.com/project/Kliche-PedalPCB.pdf",
    "ref": "R1",
    "read": "1k",
    "correction": "10k",
    "location": {"page": 1, "x0": 40, "top": 100, "x1": 98, "bottom": 110},
}


def test_a_part_report_reaches_the_sink() -> None:
    received: list[PartReport] = []
    client = TestClient(create_app(Settings(), part_reports=received.append))

    response = client.post("/api/part-reports", json=_PART_REPORT)

    assert response.status_code == 204
    assert received == [
        PartReport(
            document=_PART_REPORT["document"],  # type: ignore[arg-type]
            ref="R1",
            read="1k",
            correction="10k",
            location=Location(page=1, x0=40, top=100, x1=98, bottom=110),
            comment="",
        )
    ]


def test_part_reports_answer_503_when_not_set_up(client: TestClient) -> None:
    assert client.post("/api/part-reports", json=_PART_REPORT).status_code == 503
