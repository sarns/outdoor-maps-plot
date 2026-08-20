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
