from pathlib import Path

import pytest

from outdoor_maps_plot.elevation import ElevationGrid, ElevationRequest
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.relief_service import render_relief
from outdoor_maps_plot.service import CancellationToken, ProgressEvent, RenderCancelled
from outdoor_maps_plot.water import WaterArea, WaterError, WaterFeatures


class FakeElevationProvider:
    cache_identity = "fake"
    attribution = "Synthetic test elevations"

    def load(self, request: ElevationRequest, *, cancelled=None) -> ElevationGrid:
        values = tuple(
            tuple(float(row + column) for column in range(request.columns))
            for row in range(request.rows)
        )
        return ElevationGrid(values)


class FakeWaterProvider:
    cache_identity = "fake-water"
    attribution = "Synthetic test water"

    def load(self, projection, bounds, **kwargs) -> WaterFeatures:
        return WaterFeatures(areas=(WaterArea(((2, 2), (18, 2), (18, 18), (2, 18), (2, 2))),))


class UnavailableWaterProvider:
    cache_identity = "unavailable-water"
    attribution = "Unavailable test water"

    def load(self, projection, bounds, **kwargs) -> WaterFeatures:
        raise WaterError("test service is offline")


class PartialWaterProvider(FakeWaterProvider):
    def load(self, projection, bounds, **kwargs) -> WaterFeatures:
        features = super().load(projection, bounds, **kwargs)
        return WaterFeatures(features.areas, features.lines, complete=False)


class ProgressWaterProvider(FakeWaterProvider):
    def load(self, projection, bounds, **kwargs) -> WaterFeatures:
        kwargs["progress"](1, 2)
        kwargs["progress"](2, 2)
        return super().load(projection, bounds, **kwargs)


def _route() -> Route:
    return Route(
        name="Sample",
        segments=[[(47.0, 10.0), (47.05, 10.05), (47.1, 10.0)]],
        distance_km=15,
        ascent_m=300,
    )


def test_render_relief_creates_four_part_3mf_and_reports_progress(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    destination = tmp_path / "relief.3mf"
    result = render_relief(
        [_route()],
        destination,
        tmp_path / "cache",
        ReliefConfig(width_mm=40, depth_mm=40, mesh_pitch_mm=10),
        progress=events.append,
        elevation_provider=FakeElevationProvider(),
        water_provider=FakeWaterProvider(),
    )

    assert result.path == destination
    assert result.output_format == "3mf"
    assert result.media_type == "model/3mf"
    assert result.size_bytes > 0
    assert [event.percent for event in events] == sorted(event.percent for event in events)
    assert events[-1].phase == "finalizing"


def test_render_relief_honors_pre_cancelled_token(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()

    with pytest.raises(RenderCancelled):
        render_relief(
            [_route()],
            tmp_path / "relief.3mf",
            tmp_path / "cache",
            ReliefConfig(width_mm=40, depth_mm=40, mesh_pitch_mm=10),
            cancellation=token,
            elevation_provider=FakeElevationProvider(),
            water_provider=FakeWaterProvider(),
        )


def test_render_relief_completes_with_warning_when_water_service_is_offline(
    tmp_path: Path,
) -> None:
    events: list[ProgressEvent] = []
    result = render_relief(
        [_route()],
        tmp_path / "relief-without-water.3mf",
        tmp_path / "cache",
        ReliefConfig(width_mm=40, depth_mm=40, mesh_pitch_mm=10),
        progress=events.append,
        elevation_provider=FakeElevationProvider(),
        water_provider=UnavailableWaterProvider(),
    )

    assert result.path.is_file()
    assert result.warnings == (
        "OpenStreetMap water was unavailable; the relief was generated without water.",
    )
    assert any("without water" in event.message for event in events)


def test_render_relief_reports_partially_available_water(tmp_path: Path) -> None:
    result = render_relief(
        [_route()],
        tmp_path / "relief-with-partial-water.3mf",
        tmp_path / "cache",
        ReliefConfig(width_mm=40, depth_mm=40, mesh_pitch_mm=10),
        elevation_provider=FakeElevationProvider(),
        water_provider=PartialWaterProvider(),
    )

    assert result.path.is_file()
    assert result.warnings == (
        "Some OpenStreetMap water tiles were unavailable; water geometry may be incomplete.",
    )


def test_render_relief_reports_incremental_water_progress(tmp_path: Path) -> None:
    events: list[ProgressEvent] = []
    render_relief(
        [_route()],
        tmp_path / "relief-water-progress.3mf",
        tmp_path / "cache",
        ReliefConfig(width_mm=40, depth_mm=40, mesh_pitch_mm=10),
        progress=events.append,
        elevation_provider=FakeElevationProvider(),
        water_provider=ProgressWaterProvider(),
    )

    water_messages = [event.message for event in events if event.phase == "fetching_water"]
    assert "Loading large lakes (1/2)" in water_messages
    assert "Loading large lakes (2/2)" in water_messages
