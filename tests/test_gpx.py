from pathlib import Path

import pytest

from outdoor_maps_plot.gpx import GpxError, parse_gpx


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
