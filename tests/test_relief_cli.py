from pathlib import Path

import pytest

from outdoor_maps_plot import relief_cli
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.relief_options import ReliefConfig


def test_relief_cli_maps_print_dimensions_and_four_colors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    route = Route(
        name="Test stage",
        segments=[[(47.0, 10.0), (47.1, 10.1)]],
        distance_km=15.0,
        ascent_m=250.0,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(relief_cli, "collect_routes", lambda folder, order: [route])

    def fake_render(routes, destination, cache, config) -> None:
        captured.update(routes=routes, destination=destination, cache=cache, config=config)

    monkeypatch.setattr(relief_cli, "_render_relief", fake_render)
    relief_cli.main(
        [
            str(tmp_path),
            "--output",
            str(tmp_path / "model.stl"),
            "--width-mm",
            "180",
            "--depth-mm",
            "120",
            "--terrain-low-color",
            "#112233",
            "--terrain-high-color",
            "#445566",
            "--water-color",
            "#778899",
            "--minimum-lake-area-mm2",
            "12.5",
            "--track-color",
            "#aabbcc",
        ]
    )

    config = captured["config"]
    assert isinstance(config, ReliefConfig)
    assert config.width_mm == 180
    assert config.depth_mm == 120
    assert config.minimum_lake_area_mm2 == 12.5
    assert [config.low_color, config.high_color, config.water_color, config.track_color] == [
        "#112233",
        "#445566",
        "#778899",
        "#AABBCC",
    ]
    assert Path(captured["destination"]).suffix == ".3mf"
    assert "Created" in capsys.readouterr().out


def test_relief_cli_rejects_dimensions_over_256_mm(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        relief_cli.main([str(tmp_path), "--width-mm", "256.1"])

    assert exc_info.value.code == 2
