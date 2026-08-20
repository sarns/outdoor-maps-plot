from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from outdoor_maps_plot.poster import PosterError
from outdoor_maps_plot.service import RenderCancelled, RenderResult
from outdoor_maps_plot.web.app import create_app
from outdoor_maps_plot.web.config import WebSettings

GPX = b"""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <trk><name>Alpine stage</name><trkseg>
    <trkpt lat="47.0" lon="10.0"><ele>500</ele></trkpt>
    <trkpt lat="47.01" lon="10.0"><ele>550</ele></trkpt>
    <trkpt lat="47.02" lon="10.0"><ele>525</ele></trkpt>
  </trkseg></trk>
</gpx>
"""


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        routes,
        destination,
        cache,
        config,
        progress=None,
        cancellation=None,
    ) -> RenderResult:
        self.calls.append(
            {
                "route_count": len(routes),
                "destination": destination,
                "cache": cache,
                "config": config,
            }
        )
        if progress:
            from datetime import UTC, datetime

            from outdoor_maps_plot.service import ProgressEvent

            progress(
                ProgressEvent("drawing", 70, "secret path should be hidden", datetime.now(UTC))
            )
        if cancellation:
            cancellation.raise_if_cancelled()
        signatures = {"pdf": b"%PDF-fake", "png": b"\x89PNG\r\n\x1a\nfake", "jpeg": b"\xff\xd8fake"}
        destination.write_bytes(signatures[config.output_format])
        media = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpeg": "image/jpeg",
        }[config.output_format]
        return RenderResult(destination, config.output_format, media, destination.stat().st_size)


class FakeReliefRenderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        routes,
        destination,
        cache,
        config,
        progress=None,
        cancellation=None,
    ) -> RenderResult:
        self.calls.append(
            {
                "route_count": len(routes),
                "destination": destination,
                "cache": cache,
                "config": config,
            }
        )
        if cancellation:
            cancellation.raise_if_cancelled()
        destination.write_bytes(b"PK\x03\x04fake-3mf")
        return RenderResult(
            destination,
            "3mf",
            "model/3mf",
            destination.stat().st_size,
        )


@pytest.fixture
def settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        job_root=tmp_path / "jobs",
        cache_root=tmp_path / "cache",
        cleanup_interval_seconds=3600,
        max_file_bytes=1024 * 1024,
        max_upload_bytes=2 * 1024 * 1024,
    )


@pytest.fixture
def renderer() -> FakeRenderer:
    return FakeRenderer()


@pytest.fixture
def client(settings: WebSettings, renderer: FakeRenderer):
    app = create_app(settings, renderer)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def upload(client: TestClient, name: str = "route.gpx") -> dict[str, object]:
    response = client.post("/api/uploads", files={"files": (name, GPX, "application/gpx+xml")})
    assert response.status_code == 201, response.text
    return response.json()


def upload_fit(
    client: TestClient, fit_bytes: bytes, name: str = "activity.fit"
) -> dict[str, object]:
    response = client.post(
        "/api/uploads",
        files={"files": (name, fit_bytes, "application/vnd.ant.fit")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/api/renders/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    pytest.fail("render did not finish")


def test_config_root_health_and_security_headers(client: TestClient) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert "Outdoor Maps Plot" in root.text
    assert root.headers["x-content-type-options"] == "nosniff"
    assert root.headers["cache-control"] == "no-cache"
    version_match = re.search(r"/static/app\.js\?v=([a-f0-9]{12})", root.text)
    assert version_match
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert "application/javascript" in static.headers["content-type"]
    assert static.headers["cache-control"] == "no-cache"
    versioned_static = client.get(f"/static/app.js?v={version_match.group(1)}")
    assert versioned_static.headers["cache-control"] == ("public, max-age=31536000, immutable")

    config = client.get("/api/config")
    assert config.status_code == 200
    body = config.json()
    assert body["limits"]["max_files"] == 15
    assert body["limits"]["hard_max_files"] == 15
    assert body["route_extensions"] == [".fit", ".gpx"]
    assert all(len(style["route_palette"]) >= 5 for style in body["styles"])
    assert all(style["route_palette"][0] == style["route"] for style in body["styles"])
    assert body["defaults"]["orientation"] == "landscape"
    assert body["relief_defaults"]["width_mm"] == 240
    assert body["relief_defaults"]["depth_mm"] == 240
    assert body["relief_defaults"]["output_format"] == "3mf"
    assert body["relief_schema"]["properties"]["width_mm"]["maximum"] == 256
    assert {"pdf", "png", "jpeg"} == set(body["output_formats"])
    assert body["relief_output_formats"] == ["3mf"]
    assert all("key" not in provider for provider in body["providers"])
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_settings_hard_cap_and_lower_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMP_MAX_FILES", "16")
    with pytest.raises(ValidationError):
        WebSettings.from_env()
    monkeypatch.setenv("OMP_MAX_FILES", "4")
    settings = WebSettings.from_env()
    assert settings.max_files == 4


def test_upload_returns_summary_and_uses_generated_paths(
    client: TestClient, settings: WebSettings
) -> None:
    body = upload(client, r"C:\private\stage.gpx")
    assert body["files"][0]["display_name"] == "stage.gpx"
    assert body["summary"]["route_count"] == 1
    assert body["summary"]["point_count"] == 3
    assert body["summary"]["ascent_m"] == 50.0
    assert body["routes"][0]["name"] == "Alpine stage"

    stored = list((settings.job_root / body["upload_id"] / "input").iterdir())
    assert [path.name for path in stored] == ["001.gpx"]
    assert "stage" not in stored[0].name

    fetched = client.get(f"/api/uploads/{body['upload_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["summary"] == body["summary"]


def test_upload_accepts_fit_and_returns_route_summary(
    client: TestClient, settings: WebSettings, fit_bytes: bytes
) -> None:
    body = upload_fit(client, fit_bytes, r"C:\private\morning-ride.FIT")

    assert body["files"][0]["display_name"] == "morning-ride.FIT"
    assert body["summary"]["route_count"] == 1
    assert body["summary"]["point_count"] == 3
    assert body["summary"]["ascent_m"] == 50.0
    assert body["routes"][0]["name"] == "morning-ride"
    stored = list((settings.job_root / body["upload_id"] / "input").iterdir())
    assert [path.name for path in stored] == ["001.fit"]


def test_upload_limits_and_safe_validation_errors(
    settings: WebSettings, renderer: FakeRenderer, fit_bytes: bytes
) -> None:
    app = create_app(settings.model_copy(update={"max_files": 2}), renderer)
    with TestClient(app, raise_server_exceptions=False) as client:
        too_many = client.post(
            "/api/uploads",
            files=[("files", (f"{index}.gpx", GPX)) for index in range(3)],
        )
        assert too_many.status_code == 413
        assert too_many.json()["error"]["code"] == "too_many_files"

        wrong_type = client.post("/api/uploads", files={"files": ("notes.txt", GPX)})
        assert wrong_type.status_code == 422
        assert wrong_type.json()["error"]["code"] == "invalid_file_type"

        malformed = client.post(
            "/api/uploads",
            files={"files": ("secret.gpx", b"<gpx><broken>")},
        )
        assert malformed.status_code == 422
        payload = malformed.json()
        assert payload["error"]["code"] == "invalid_gpx"
        assert str(settings.job_root) not in malformed.text

        invalid_fit = client.post(
            "/api/uploads",
            files={"files": ("broken.fit", fit_bytes[:-1])},
        )
        assert invalid_fit.status_code == 422
        assert invalid_fit.json()["error"]["code"] == "invalid_fit"

    small = settings.model_copy(update={"max_file_bytes": 128, "max_upload_bytes": 256})
    with TestClient(create_app(small, renderer), raise_server_exceptions=False) as client:
        oversized = client.post("/api/uploads", files={"files": ("large.gpx", GPX)})
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "file_too_large"


def test_hard_maximum_rejects_sixteen_files(settings: WebSettings, renderer: FakeRenderer) -> None:
    with TestClient(create_app(settings, renderer), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/uploads",
            files=[("files", (f"{index}.gpx", GPX)) for index in range(16)],
        )
    assert response.status_code == 413
    assert response.json()["error"]["details"] == [{"limit": 15}]


def test_aggregate_point_budget_is_enforced(settings: WebSettings, renderer: FakeRenderer) -> None:
    limited = settings.model_copy(update={"max_points_total": 5})
    with TestClient(create_app(limited, renderer), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/uploads",
            files=[
                ("files", ("one.gpx", GPX)),
                ("files", ("two.gpx", GPX)),
            ],
        )
    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "too_many_points",
        "message": "The uploaded GPX data contains too many points.",
        "details": [{"limit": 5}],
    }


@pytest.mark.parametrize(
    ("output_format", "media_type", "signature"),
    [
        ("pdf", "application/pdf", b"%PDF"),
        ("png", "image/png", b"\x89PNG"),
        ("jpeg", "image/jpeg", b"\xff\xd8"),
    ],
)
def test_final_render_status_and_download(
    client: TestClient,
    output_format: str,
    media_type: str,
    signature: bytes,
) -> None:
    uploaded = upload(client)
    accepted = client.post(
        "/api/renders",
        json={
            "upload_id": uploaded["upload_id"],
            "mode": "final",
            "config": {"output_format": output_format},
        },
    )
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    result = wait_for_terminal(client, job_id)
    assert result["status"] == "succeeded"
    assert result["progress"]["percent"] == 100
    assert result["artifact"]["media_type"] == media_type
    response = client.get(result["artifact"]["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(signature)


def test_preview_enforces_server_owned_output_limits(
    client: TestClient, renderer: FakeRenderer
) -> None:
    uploaded = upload(client)
    accepted = client.post(
        "/api/renders",
        json={
            "upload_id": uploaded["upload_id"],
            "mode": "preview",
            "config": {
                "output_format": "jpeg",
                "dpi": 900,
                "basemap_width": 9000,
                "max_tiles": 500,
                "route_color": "#2b6cb0",
            },
        },
    )
    result = wait_for_terminal(client, accepted.json()["job_id"])
    assert result["status"] == "succeeded"
    effective = renderer.calls[-1]["config"]
    assert effective.output_format == "png"
    assert effective.dpi == 96
    assert effective.basemap_width == 1200
    assert effective.max_tiles == 100
    assert effective.route_color == "#2B6CB0"


def test_custom_paper_size_reaches_renderer(client: TestClient, renderer: FakeRenderer) -> None:
    uploaded = upload(client)
    accepted = client.post(
        "/api/renders",
        json={
            "upload_id": uploaded["upload_id"],
            "mode": "final",
            "config": {"paper_size": "300x400mm"},
        },
    )

    result = wait_for_terminal(client, accepted.json()["job_id"])

    assert result["status"] == "succeeded"
    assert renderer.calls[-1]["config"].paper_size == "300X400MM"


def test_relief_render_uses_separate_config_and_renderer(settings: WebSettings) -> None:
    poster_renderer = FakeRenderer()
    relief_renderer = FakeReliefRenderer()
    app = create_app(settings, poster_renderer, relief_renderer)
    with TestClient(app, raise_server_exceptions=False) as client:
        uploaded = upload(client)
        accepted = client.post(
            "/api/renders",
            json={
                "upload_id": uploaded["upload_id"],
                "product_kind": "relief",
                "mode": "final",
                "config": {
                    "width_mm": 180,
                    "depth_mm": 120,
                    "low_color": "#112233",
                    "mid_color": "#445566",
                    "high_color": "#778899",
                    "track_color": "#AABBCC",
                },
            },
        )
        assert accepted.status_code == 202, accepted.text
        result = wait_for_terminal(client, accepted.json()["job_id"])
        download = client.get(result["artifact"]["download_url"])

    assert result["status"] == "succeeded"
    assert result["product_kind"] == "relief"
    assert result["artifact"]["media_type"] == "model/3mf"
    assert result["artifact"]["filename"].endswith(".3mf")
    assert download.content.startswith(b"PK\x03\x04")
    assert not poster_renderer.calls
    config = relief_renderer.calls[-1]["config"]
    assert config.width_mm == 180
    assert config.depth_mm == 120
    assert config.output_format == "3mf"


def test_relief_preview_is_explicitly_unavailable(settings: WebSettings) -> None:
    app = create_app(settings, FakeRenderer(), FakeReliefRenderer())
    with TestClient(app, raise_server_exceptions=False) as client:
        uploaded = upload(client)
        response = client.post(
            "/api/renders",
            json={
                "upload_id": uploaded["upload_id"],
                "product_kind": "relief",
                "mode": "preview",
                "config": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "relief_preview_unavailable"


@pytest.mark.parametrize(("field", "value"), [("width_mm", 256.1), ("depth_mm", 300)])
def test_relief_rejects_dimensions_over_build_area(
    settings: WebSettings, field: str, value: float
) -> None:
    app = create_app(settings, FakeRenderer(), FakeReliefRenderer())
    with TestClient(app, raise_server_exceptions=False) as client:
        uploaded = upload(client)
        response = client.post(
            "/api/renders",
            json={
                "upload_id": uploaded["upload_id"],
                "product_kind": "relief",
                "config": {field: value},
            },
        )

    assert response.status_code == 422


def test_expected_poster_errors_are_actionable(
    settings: WebSettings, renderer: FakeRenderer
) -> None:
    def failing_renderer(*args, **kwargs):
        raise PosterError("The selected extent requires too many map tiles; lower the zoom.")

    app = create_app(settings, failing_renderer)
    with TestClient(app, raise_server_exceptions=False) as local:
        uploaded = upload(local)
        accepted = local.post(
            "/api/renders",
            json={"upload_id": uploaded["upload_id"], "config": {"paper_size": "300x400mm"}},
        )
        job_id = accepted.json()["job_id"]
        result = wait_for_terminal(local, job_id)
        events = local.get(f"/api/renders/{job_id}/events")

    assert result["status"] == "failed"
    expected_error = {
        "code": "poster_error",
        "message": "The selected extent requires too many map tiles; lower the zoom.",
        "details": [],
    }
    assert result["error"] == expected_error
    event_data = next(
        line.removeprefix("data: ")
        for line in events.text.splitlines()
        if line.startswith("data: ")
    )
    assert json.loads(event_data)["error"] == expected_error


def test_render_errors_are_safe(client: TestClient, settings: WebSettings) -> None:
    def failing_renderer(*args, **kwargs):
        raise RuntimeError(f"provider key=top-secret path={settings.job_root}")

    app = create_app(settings, failing_renderer)
    with TestClient(app, raise_server_exceptions=False) as local:
        uploaded = upload(local)
        accepted = local.post(
            "/api/renders",
            json={"upload_id": uploaded["upload_id"], "config": {}},
        )
        result = wait_for_terminal(local, accepted.json()["job_id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == "render_failed"
    assert "secret" not in str(result)
    assert str(settings.job_root) not in str(result)


def test_queue_full_and_cooperative_cancellation(settings: WebSettings) -> None:
    release = threading.Event()
    started = threading.Event()

    def blocking_renderer(routes, destination, cache, config, progress=None, cancellation=None):
        started.set()
        while not release.wait(0.01):
            if cancellation and cancellation.cancelled:
                raise RenderCancelled("cancelled")
        destination.write_bytes(b"%PDF")
        return RenderResult(destination, "pdf", "application/pdf", 4)

    bounded = settings.model_copy(update={"max_concurrent_jobs": 1, "max_queued_jobs": 1})
    with TestClient(
        create_app(bounded, blocking_renderer), raise_server_exceptions=False
    ) as client:
        uploaded = upload(client)
        payload = {"upload_id": uploaded["upload_id"], "config": {}}
        first = client.post("/api/renders", json=payload)
        assert first.status_code == 202
        assert started.wait(1)
        second = client.post("/api/renders", json=payload)
        assert second.status_code == 202
        third = client.post("/api/renders", json=payload)
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "render_queue_full"

        cancelled = client.delete(f"/api/renders/{first.json()['job_id']}")
        assert cancelled.status_code == 200
        result = wait_for_terminal(client, first.json()["job_id"])
        assert result["status"] == "cancelled"
        release.set()


def test_upload_delete_and_cross_origin_protection(client: TestClient) -> None:
    uploaded = upload(client)
    rejected = client.post(
        "/api/renders",
        headers={"Origin": "https://evil.example"},
        json={"upload_id": uploaded["upload_id"], "config": {}},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "origin_rejected"

    deleted = client.delete(f"/api/uploads/{uploaded['upload_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    missing = client.get(f"/api/uploads/{uploaded['upload_id']}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "upload_not_found"


def test_unknown_routes_use_error_schema(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "The requested resource was not found.",
            "details": [],
        }
    }
