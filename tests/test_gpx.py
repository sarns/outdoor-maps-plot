from pathlib import Path

import pytest

from outdoor_maps_plot.gpx import (
    GpxError,
    PointBudget,
    collect_routes,
    parse_fit,
    parse_gpx,
)


def test_parse_track_and_statistics(tmp_path: Path) -> None:
    gpx = tmp_path / "stage.gpx"
    gpx.write_text(
        """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <trk>
    <name>Test stage</name>
    <trkseg>
      <trkpt lat="47.0" lon="10.0"><ele>500</ele></trkpt>
      <trkpt lat="47.01" lon="10.0"><ele>550</ele></trkpt>
      <trkpt lat="47.02" lon="10.0"><ele>525</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
""",
        encoding="utf-8",
    )

    routes = parse_gpx(gpx)

    assert len(routes) == 1
    assert routes[0].name == "Test stage"
    assert routes[0].distance_km == pytest.approx(2.224, rel=0.01)
    assert routes[0].ascent_m == 50
    assert routes[0].start == (47.0, 10.0)
    assert routes[0].end == (47.02, 10.0)


def test_invalid_coordinate_has_context(tmp_path: Path) -> None:
    gpx = tmp_path / "bad.gpx"
    gpx.write_text(
        '<gpx><rte><rtept lat="north" lon="10"/><rtept lat="47" lon="10"/></rte></gpx>',
        encoding="utf-8",
    )

    with pytest.raises(GpxError, match="bad.gpx"):
        parse_gpx(gpx)


def test_rejects_xml_entities(tmp_path: Path) -> None:
    gpx = tmp_path / "entity.gpx"
    gpx.write_text(
        """<!DOCTYPE gpx [<!ENTITY payload "not allowed">]>
<gpx><rte><name>&payload;</name>
<rtept lat="47" lon="10"/><rtept lat="48" lon="10"/></rte></gpx>""",
        encoding="utf-8",
    )

    with pytest.raises(GpxError, match="safe XML"):
        parse_gpx(gpx)


def test_enforces_per_file_point_limit(tmp_path: Path) -> None:
    gpx = tmp_path / "large.gpx"
    gpx.write_text(
        """<gpx><rte>
<rtept lat="47" lon="10"/>
<rtept lat="48" lon="10"/>
<rtept lat="49" lon="10"/>
</rte></gpx>""",
        encoding="utf-8",
    )

    with pytest.raises(GpxError, match="point limit of 2 exceeded"):
        parse_gpx(gpx, max_points=2)


def test_enforces_shared_aggregate_point_budget(tmp_path: Path) -> None:
    first = tmp_path / "first.gpx"
    second = tmp_path / "second.gpx"
    document = '<gpx><rte><rtept lat="47" lon="10"/><rtept lat="48" lon="10"/></rte></gpx>'
    first.write_text(document, encoding="utf-8")
    second.write_text(document, encoding="utf-8")
    budget = PointBudget(maximum=3)

    parse_gpx(first, point_budget=budget)
    with pytest.raises(GpxError, match="aggregate GPX point limit of 3 exceeded"):
        parse_gpx(second, point_budget=budget)


def test_parse_fit_records_and_statistics(tmp_path: Path, fit_bytes: bytes) -> None:
    fit = tmp_path / "morning-ride.fit"
    fit.write_bytes(fit_bytes)

    routes = parse_fit(fit)

    assert len(routes) == 1
    assert routes[0].name == "morning-ride"
    assert routes[0].distance_km == pytest.approx(2.224, rel=0.01)
    assert routes[0].ascent_m == 50
    assert routes[0].start == pytest.approx((47.0, 10.0))
    assert routes[0].end == pytest.approx((47.02, 10.0))


def test_fit_crc_and_point_limits_are_enforced(tmp_path: Path, fit_bytes: bytes) -> None:
    invalid = tmp_path / "invalid.fit"
    invalid.write_bytes(fit_bytes[:-1] + bytes((fit_bytes[-1] ^ 0xFF,)))
    with pytest.raises(GpxError, match="valid FIT"):
        parse_fit(invalid)

    large = tmp_path / "large.fit"
    large.write_bytes(fit_bytes)
    with pytest.raises(GpxError, match="FIT point limit of 2 exceeded"):
        parse_fit(large, max_points=2)


def test_collect_routes_discovers_gpx_and_fit_case_insensitively(
    tmp_path: Path, fit_bytes: bytes
) -> None:
    fit = tmp_path / "activity.FIT"
    fit.write_bytes(fit_bytes)
    gpx = tmp_path / "route.GPX"
    gpx.write_text(
        '<gpx><rte><rtept lat="46" lon="10"/><rtept lat="46.1" lon="10"/></rte></gpx>',
        encoding="utf-8",
    )

    routes = collect_routes(tmp_path, order="input")

    assert [route.name for route in routes] == ["activity", "route"]
