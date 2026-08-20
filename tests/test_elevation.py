from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from outdoor_maps_plot.elevation import (
    ElevationCancelled,
    ElevationError,
    ElevationGrid,
    ElevationRequest,
    RegularGeoGrid,
    RegularGridElevationProvider,
    TerrariumElevationProvider,
    normalize_elevation,
)
from outdoor_maps_plot.projection import LocalMetricProjection, MetricBounds


def _request(*, columns: int = 3, rows: int = 3) -> ElevationRequest:
    return ElevationRequest(
        LocalMetricProjection(47.0, 10.0), MetricBounds(-10, -10, 10, 10), columns, rows
    )


def test_request_size_is_derived_from_model_and_bounded() -> None:
    projection = LocalMetricProjection(47.0, 10.0)
    bounds = MetricBounds(-100, -100, 100, 100)

    request = ElevationRequest.for_model(
        projection, bounds, width_mm=240, depth_mm=120, mesh_pitch_mm=0.8
    )

    assert (request.columns, request.rows) == (301, 151)
    with pytest.raises(ElevationError, match="needs"):
        ElevationRequest.for_model(
            projection,
            bounds,
            width_mm=240,
            depth_mm=240,
            mesh_pitch_mm=0.1,
            max_cells=100_000,
        )


def test_regular_grid_resamples_and_replaces_nodata() -> None:
    source = RegularGeoGrid(
        south=46.99,
        west=9.99,
        latitude_step=0.01,
        longitude_step=0.01,
        values_m=((100.0, 110.0, 120.0), (130.0, None, 150.0), (160.0, 170.0, 180.0)),
    )
    provider = RegularGridElevationProvider(source)

    grid = provider.load(_request())

    assert grid.rows == 3
    assert grid.columns == 3
    assert all(value == pytest.approx(value) for row in grid.values_m for value in row)
    assert 100 <= grid.minimum_m <= grid.maximum_m <= 180


def test_local_json_grid_is_bounded_and_has_content_addressed_identity(tmp_path: Path) -> None:
    path = tmp_path / "elevation.json"
    path.write_text(
        json.dumps(
            {
                "south": 46.9,
                "west": 9.9,
                "latitude_step": 0.1,
                "longitude_step": 0.1,
                "values_m": [[1, 2], [3, 4]],
            }
        ),
        encoding="utf-8",
    )

    first = RegularGridElevationProvider.from_json(path)
    second = RegularGridElevationProvider.from_json(path)

    assert first.cache_identity == second.cache_identity
    assert _request().cache_key(first.cache_identity) == _request().cache_key(second.cache_identity)
    with pytest.raises(ElevationError, match="exceeds"):
        RegularGridElevationProvider.from_json(path, max_bytes=2)


def test_provider_honours_cancellation() -> None:
    provider = RegularGridElevationProvider(
        RegularGeoGrid(46.0, 9.0, 1.0, 1.0, ((1.0, 2.0), (3.0, 4.0)))
    )

    with pytest.raises(ElevationCancelled):
        provider.load(_request(), cancelled=lambda: True)


def test_normalization_preserves_source_range_and_handles_flat_grid() -> None:
    normalized = normalize_elevation(
        ElevationGrid(((100.0, 150.0), (125.0, 200.0))), relief_height_mm=20
    )
    flat = normalize_elevation(ElevationGrid(((5.0, 5.0), (5.0, 5.0))), relief_height_mm=20)

    assert normalized.source_minimum_m == 100
    assert normalized.source_maximum_m == 200
    assert normalized.values_mm == ((0.0, 10.0), (5.0, 20.0))
    assert flat.values_mm == ((0.0, 0.0), (0.0, 0.0))


def test_terrarium_decoding_raw_tile_cache_and_limits(tmp_path: Path) -> None:
    image = Image.new("RGB", (256, 256), (129, 244, 0))  # 500 metres
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    calls: list[str] = []

    def fetch(url: str, limit: int) -> bytes:
        calls.append(url)
        assert len(encoded.getvalue()) <= limit
        return encoded.getvalue()

    provider = TerrariumElevationProvider(zoom=8, cache_dir=tmp_path, max_tiles=8, fetcher=fetch)
    grid = provider.load(_request(columns=2, rows=2))

    assert all(value == pytest.approx(500.0) for row in grid.values_m for value in row)
    assert calls

    cached_provider = TerrariumElevationProvider(
        zoom=8,
        cache_dir=tmp_path,
        max_tiles=8,
        fetcher=lambda _url, _limit: (_ for _ in ()).throw(AssertionError("network used")),
    )
    assert cached_provider.load(_request(columns=2, rows=2)).minimum_m == pytest.approx(500)


def test_terrarium_rejects_unsupported_zoom() -> None:
    with pytest.raises(ElevationError, match="between 0 and 15"):
        TerrariumElevationProvider(zoom=16)
