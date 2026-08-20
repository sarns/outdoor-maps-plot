import json

import pytest

from outdoor_maps_plot.projection import LocalMetricProjection, MetricBounds
from outdoor_maps_plot.water import OpenStreetMapWaterProvider, WaterCancelled, WaterError


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


def test_osm_water_provider_maps_lakes_and_rivers_to_model_space() -> None:
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
        minimum_line_width_mm=1.2,
    )

    assert len(features.areas) == 1
    assert len(features.lines) == 1
    assert features.lines[0].width_mm == pytest.approx(2.0)
    assert b"natural%22%3D%22water" in requests[0]
    assert all(0 < value < 100 for point in features.areas[0].outer_mm for value in point)


def test_osm_water_provider_honors_cancellation_before_fetch() -> None:
    provider = OpenStreetMapWaterProvider(fetcher=lambda *_args: _document())
    with pytest.raises(WaterCancelled):
        provider.load(
            LocalMetricProjection(47.0, 10.0),
            MetricBounds(-500, -500, 500, 500),
            width_mm=100,
            depth_mm=100,
            minimum_line_width_mm=1.2,
            cancelled=lambda: True,
        )


def test_osm_water_provider_uses_next_endpoint_after_network_failure() -> None:
    requested_urls: list[str] = []

    def fetch(url: str, _body: bytes, _limit: int) -> bytes:
        requested_urls.append(url)
        if url.endswith("first.example/api"):
            raise WaterError("first endpoint unavailable")
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://first.example/api", "https://second.example/api"),
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-500, -500, 500, 500),
        width_mm=100,
        depth_mm=100,
        minimum_line_width_mm=1.2,
    )

    assert requested_urls == ["https://first.example/api", "https://second.example/api"]
    assert not features.empty


def test_osm_water_provider_tiles_large_extents_and_deduplicates_features() -> None:
    requests: list[bytes] = []

    def fetch(_url: str, body: bytes, _limit: int) -> bytes:
        requests.append(body)
        return _document()

    provider = OpenStreetMapWaterProvider(
        endpoints=("https://water.example/api",),
        max_query_tiles=16,
        target_tile_span_km=50,
        fetcher=fetch,
    )
    features = provider.load(
        LocalMetricProjection(47.0, 10.0),
        MetricBounds(-100_000, -100_000, 100_000, 100_000),
        width_mm=100,
        depth_mm=100,
        minimum_line_width_mm=1.2,
    )

    assert 1 < len(requests) <= 16
    assert all(b"stream" not in body for body in requests)
    assert len(features.areas) == 1
    assert len(features.lines) == 1
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
        minimum_line_width_mm=1.2,
    )

    assert queries_seen > 1
    assert not features.empty
    assert not features.complete
