from pathlib import Path

import pytest

from outdoor_maps_plot.elevation import ElevationGrid, ElevationRequest
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.relief_service import render_relief
from outdoor_maps_plot.service import CancellationToken, ProgressEvent, RenderCancelled


class FakeElevationProvider:
    cache_identity = "fake"
    attribution = "Synthetic test elevations"

    def load(self, request: ElevationRequest, *, cancelled=None) -> ElevationGrid:
        values = tuple(
            tuple(float(row + column) for column in range(request.columns))
            for row in range(request.rows)
        )
        return ElevationGrid(values)


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
        )
