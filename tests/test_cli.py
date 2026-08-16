from pathlib import Path

from outdoor_maps_plot import cli
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.options import PosterConfig


def test_cli_maps_arguments_to_shared_configuration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    route = Route(
        name="Test stage",
        segments=[[(47.0, 10.0), (47.1, 10.1)]],
        distance_km=15.0,
        ascent_m=250.0,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "collect_routes", lambda folder, order: [route])

    def fake_render(
        routes: list[Route],
        destination: Path,
        cache: Path,
        config: PosterConfig,
    ) -> None:
        captured.update(
            routes=routes,
            destination=destination,
            cache=cache,
            config=config,
        )

    monkeypatch.setattr(cli, "render_poster", fake_render)
    cli.main(
        [
            str(tmp_path),
            "--output",
            str(tmp_path / "poster.pdf"),
            "--format",
            "png",
            "--title",
            "Test adventure",
            "--paper-size",
            "300x400mm",
            "--orientation",
            "portrait",
            "--style",
            "cool-minimal",
            "--route-order",
            "input",
            "--route-color",
            "#2b6cb0",
            "--route-color-mode",
            "palette",
            "--padding",
            "12",
        ]
    )

    config = captured["config"]
    assert isinstance(config, PosterConfig)
    assert config.orientation == "portrait"
    assert config.paper_size == "300X400MM"
    assert config.style_name == "cool-minimal"
    assert config.route_order == "input"
    assert config.route_color == "#2B6CB0"
    assert config.route_color_mode == "palette"
    assert config.padding_percent == 12
    assert config.output_format == "png"
    assert Path(captured["destination"]).suffix == ".png"
    assert "Created" in capsys.readouterr().out


def test_cli_lists_styles_without_reading_input(capsys) -> None:
    cli.main(["--list-styles"])

    output = capsys.readouterr().out
    assert "classic" in output
    assert "dark-topographic" in output
