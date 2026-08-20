import pytest

from outdoor_maps_plot.projection import (
    LocalMetricProjection,
    fit_metric_bounds,
    prepare_model_route,
)


def test_local_projection_has_realistic_wgs84_scale_and_round_trips() -> None:
    projection = LocalMetricProjection(47.0, 10.0)

    north = projection.project((47.01, 10.0))
    east = projection.project((47.0, 10.01))

    assert north == pytest.approx((0.0, 1111.7), abs=0.5)
    assert east == pytest.approx((760.6, 0.0), abs=0.5)
    assert projection.unproject(east) == pytest.approx((47.0, 10.01))


def test_centering_handles_date_line_routes() -> None:
    projection = LocalMetricProjection.centered_on([(10.0, 179.9), (10.0, -179.9)])
    first = projection.project((10.0, 179.9))
    second = projection.project((10.0, -179.9))

    assert abs(first[0] - second[0]) < 25_000


def test_bounds_are_padded_and_expanded_without_stretching() -> None:
    bounds = fit_metric_bounds(
        [(0.0, 0.0), (100.0, 200.0)], target_aspect=1.0, padding_fraction=0.1
    )

    assert bounds.width == pytest.approx(240.0)
    assert bounds.height == pytest.approx(240.0)
    assert (bounds.min_x + bounds.max_x) / 2 == pytest.approx(50.0)
    assert (bounds.min_y + bounds.max_y) / 2 == pytest.approx(100.0)


def test_prepare_model_route_preserves_aspect_and_supports_smaller_sizes() -> None:
    model = prepare_model_route(
        [[(47.0, 10.0), (47.01, 10.02)]],
        width_mm=120,
        depth_mm=80,
        padding_fraction=0,
    )

    assert model.terrain_bounds_m.width / model.terrain_bounds_m.height == pytest.approx(1.5)
    for x, y in model.segments_mm[0]:
        assert 0 <= x <= 120
        assert 0 <= y <= 80


def test_model_dimensions_cannot_exceed_build_space() -> None:
    with pytest.raises(ValueError, match="cannot exceed 256 mm"):
        prepare_model_route([[(47.0, 10.0), (47.1, 10.1)]], width_mm=257, depth_mm=100)
