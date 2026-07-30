from pathlib import Path

import pytest
from PIL import Image

from outdoor_maps_plot import poster
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.options import PosterConfig
from outdoor_maps_plot.poster import world_pixel
from outdoor_maps_plot.service import (
    CancellationToken,
    ProgressEvent,
    RenderCancelled,
    render_poster,
)


def _route() -> Route:
    return Route(
        name="Sample stage",
        segments=[[(47.0, 10.0), (47.1, 10.1), (47.2, 10.0)]],
        distance_km=25.0,
        ascent_m=750.0,
    )


def _offline_basemap(tmp_path: Path, route: Route):
    def fake_basemap(*args: object, **kwargs: object):
        image_path = tmp_path / "basemap.png"
        Image.new("RGB", (600, 800), "#d9dfd5").save(image_path)
        pixels = [world_pixel(point, 10) for point in route.points]
        return image_path, (
            min(point[0] for point in pixels) - 10,
            min(point[1] for point in pixels) - 10,
            max(point[0] for point in pixels) + 10,
            max(point[1] for point in pixels) + 10,
        )

    return fake_basemap


def test_service_reports_stable_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = _route()
    monkeypatch.setattr(poster, "make_basemap", _offline_basemap(tmp_path, route))
    events: list[ProgressEvent] = []
    destination = tmp_path / "poster.pdf"

    result = render_poster(
        [route],
        destination,
        tmp_path / "cache",
        PosterConfig(paper_size="A5", basemap_width=512),
        progress=events.append,
    )

    assert result.path == destination
    assert result.media_type == "application/pdf"
    assert [event.percent for event in events] == sorted(event.percent for event in events)
    assert events[0].phase == "validating"
    assert events[-1].phase == "finalizing"
    assert events[-1].percent == 100
    assert "10.0" not in " ".join(event.message for event in events)


def test_service_honors_pre_cancelled_token(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()

    with pytest.raises(RenderCancelled):
        render_poster(
            [_route()],
            tmp_path / "poster.pdf",
            tmp_path / "cache",
            PosterConfig(),
            cancellation=token,
        )


def test_service_cancels_during_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = _route()
    token = CancellationToken()
    monkeypatch.setattr(poster, "make_basemap", _offline_basemap(tmp_path, route))

    def cancel_during_drawing(event: ProgressEvent) -> None:
        if event.phase == "drawing":
            token.cancel()

    with pytest.raises(RenderCancelled):
        render_poster(
            [route],
            tmp_path / "poster.pdf",
            tmp_path / "cache",
            PosterConfig(paper_size="A5", basemap_width=512),
            progress=cancel_during_drawing,
            cancellation=token,
        )
