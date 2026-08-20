import io
import json
import threading

import pytest
from PIL import Image, ImageDraw

from outdoor_maps_plot.projection import LocalMetricProjection, MetricBounds
from outdoor_maps_plot.water import (
    OSM_WATER_COLOR,
    OpenStreetMapLakeTileProvider,
    OpenStreetMapWaterProvider,
    WaterCancelled,
    WaterError,
)


def _document() -> bytes:
    return json.dumps(
        {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "tags": {"natural": "water", "water": "lake"},
                    "geometry": [
                        {"lat": 46.999, "lon": 9.999},
                        {"lat": 46.999, "lon": 10.001},
                        {"lat": 47.001, "lon": 10.001},
                        {"lat": 47.001, "lon": 9.999},
                        {"lat": 46.999, "lon": 9.999},
                    ],
                },
                {
                    "type": "way",
                    "id": 2,
                    "tags": {"waterway": "river", "width": "20"},
                    "geometry": [
                        {"lat": 46.998, "lon": 10.0},
                        {"lat": 47.002, "lon": 10.0},
                    ],
                },
            ]
        }
    ).encode()


def _map_tile(*, large_lake: bool = True, thin_river: bool = False) -> bytes:
    image = Image.new("RGB", (256, 256), "#f2efe9")
    draw = ImageDraw.Draw(image)
    if large_lake:
        draw.rectangle((0, 0, 255, 255), fill=OSM_WATER_COLOR)
    if thin_river:
        draw.line((0, 128, 255, 128), fill=OSM_WATER_COLOR, width=1)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_lake_tile_provider_returns_a_normalized_large_lake_mask() -> None:
    urls: list[str] = []

    def fetch(url: str, _limit: int) -> bytes:
        urls.append(url)
        return _map_tile()

    features = OpenStreetMapLakeTileProvider(
        max_zoom=8,
        mask_size=64,
        fetcher=fetch,
    ).load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=9,
    )

    assert urls
    assert not features.empty
    assert len(features.raster_mask) == 64
    assert len(features.raster_mask[0]) == 64
    assert sum(map(sum, features.raster_mask)) > 3_000


def test_lake_tile_provider_removes_a_thin_waterway() -> None:
    features = OpenStreetMapLakeTileProvider(
        max_zoom=8,
        mask_size=64,
        fetcher=lambda *_args: _map_tile(large_lake=False, thin_river=True),
    ).load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=1,
    )

    assert features.empty


def test_osm_water_provider_maps_only_large_lakes_to_model_space() -> None:
    requests: list[bytes] = []

    def fetch(_url: str, body: bytes, _limit: int) -> bytes:
        requests.append(body)
        return _document()

    provider = OpenStreetMapWaterProvider(fetcher=fetch)
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=9,
    )

    assert len(features.areas) == 1
    assert not features.lines
    assert b"natural%22%3D%22water" in requests[0]
    assert b"waterway" not in requests[0]
    assert all(0 < value < 100 for point in features.areas[0].outer_mm for value in point)


def test_osm_water_provider_honors_cancellation_before_fetch() -> None:
    provider = OpenStreetMapWaterProvider(fetcher=lambda *_args: _document())
    with pytest.raises(WaterCancelled):
        provider.load(
            LocalMetricProjection(47.0, 10.0),
            MetricBounds(-500, -500, 500, 500),
            width_mm=100,
            depth_mm=100,
            minimum_area_mm2=9,
            cancelled=lambda: True,
        )


def test_osm_water_provider_uses_next_endpoint_after_network_failure() -> None:
    requested_urls: list[str] = []

    def fetch(url: str, _body: bytes, _limit: int) -> bytes:
        requested_urls.append(url)
        if len(requested_urls) == 1:
            raise WaterError("selected endpoint unavailable")
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://first.example/api", "https://second.example/api"),
        max_endpoint_attempts=2,
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=9,
    )

    assert len(requested_urls) == 2
    assert len(set(requested_urls)) == 2
    assert not features.empty


def test_osm_water_provider_tiles_large_extents_and_deduplicates_features() -> None:
    requests: list[bytes] = []
    progress: list[tuple[int, int]] = []

    def fetch(_url: str, body: bytes, _limit: int) -> bytes:
        requests.append(body)
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://water.example/api",),
        max_query_tiles=4,
        target_tile_span_km=50,
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-100_000, -100_000, 100_000, 100_000),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=0.0001,
        progress=lambda completed, total: progress.append((completed, total)),
    )

    assert 1 < len(requests) <= 4
    assert progress == [(index, len(requests)) for index in range(1, len(requests) + 1)]
    assert all(b"waterway" not in body for body in requests)
    assert all(b"lake%7Creservoir" in body for body in requests)
    assert len(features.areas) == 1
    assert not features.lines
    assert features.complete


def test_osm_water_provider_returns_partial_result_when_one_tile_fails() -> None:
    queries_seen = 0

    def fetch(_url: str, _body: bytes, _limit: int) -> bytes:
        nonlocal queries_seen
        queries_seen += 1
        if queries_seen > 1:
            raise WaterError("tile unavailable")
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://water.example/api",),
        max_query_tiles=4,
        target_tile_span_km=50,
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-50_000, -50_000, 50_000, 50_000),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=0.0001,
    )

    assert queries_seen > 1
    assert not features.empty
    assert not features.complete


def test_osm_water_provider_fetches_tiles_concurrently() -> None:
    rendezvous = threading.Barrier(4, timeout=1)

    def fetch(_url: str, _body: bytes, _limit: int) -> bytes:
        rendezvous.wait()
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://water.example/api",),
        max_query_tiles=4,
        target_tile_span_km=50,
        max_workers=4,
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-50_000, -50_000, 50_000, 50_000),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=0.0001,
    )

    assert not features.empty


def test_osm_water_provider_enforces_total_deadline() -> None:
    release_later = threading.Event()

    def fetch(_url: str, _body: bytes, _limit: int) -> bytes:
        release_later.wait(0.05)
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://water.example/api",),
        total_timeout_seconds=0.01,
        max_query_tiles=4,
        target_tile_span_km=50,
        max_workers=4,
        fetcher=fetch,
    )
    with pytest.raises(WaterError, match="any map tile"):
        provider.load(
            LocalMetricProjection(47.0, 10.0),
            MetricBounds(-50_000, -50_000, 50_000, 50_000),
            width_mm=100,
            depth_mm=100,
            minimum_area_mm2=9,
        )


def test_osm_water_provider_omits_lakes_below_printed_area_threshold() -> None:
    provider = OpenStreetMapWaterProvider(fetcher=lambda *_args: _document())
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_area_mm2=10_000,
    )

    assert features.empty
