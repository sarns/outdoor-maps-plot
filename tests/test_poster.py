from pathlib import Path

import pytest
from PIL import Image
from reportlab.lib.units import mm

from outdoor_maps_plot import poster
from outdoor_maps_plot.gpx import Route
from outdoor_maps_plot.poster import (
    PosterError,
    PosterOptions,
    create_poster,
    parse_page_size,
    resolve_output,
    simplify_line,
    world_pixel,
)


def test_named_and_custom_page_sizes() -> None:
    assert parse_page_size("a3")[0] == pytest.approx(297 * mm, rel=0.001)
    assert parse_page_size("300x400mm") == pytest.approx((300 * mm, 400 * mm))


def test_invalid_page_size() -> None:
    with pytest.raises(PosterError, match="Invalid paper size"):
        parse_page_size("large")


def test_output_format_inference_and_override() -> None:
    assert resolve_output(Path("poster.png"), None) == (Path("poster.png"), "png")
    assert resolve_output(Path("poster.pdf"), "jpeg") == (Path("poster.jpg"), "jpeg")


def test_world_pixel_origin() -> None:
    assert world_pixel((0.0, 0.0), 0) == pytest.approx((128.0, 128.0))


def test_small_route_uses_higher_resolution_source_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested_urls: list[str] = []

    def fake_download(url: str, path: Path, **kwargs: object) -> None:
        requested_urls.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (256, 256), "#d9dfd5").save(path)

    monkeypatch.setattr(poster, "_download_tile", fake_download)
    basemap, bounds = poster.make_basemap(
        [(50.0195, 7.1127), (50.0460, 7.1558)],
        target_aspect=1.6,
        cache=tmp_path / "cache",
        style_name="classic",
        provider="opentopo",
        zoom=10,
        padding=0.06,
        basemap_width=1200,
        max_tiles=100,
    )

    with Image.open(basemap) as image:
        assert image.width >= 1200
    assert bounds == poster._fit_bounds([(50.0195, 7.1127), (50.0460, 7.1558)], 10, 1.6, 0.06)
    assert requested_urls
    assert all(int(url.rsplit("/", 3)[-3]) > 10 for url in requested_urls)


def test_line_simplification() -> None:
    points = [(0.0, 0.0), (1.0, 0.01), (2.0, 0.0)]
    assert simplify_line(points, 0.1) == [(0.0, 0.0), (2.0, 0.0)]
    assert simplify_line(points, 0.001) == points


def test_route_colors_use_and_cycle_the_style_palette() -> None:
    config = poster.PosterConfig(style_name="cool-minimal", route_color_mode="palette")

    colors = poster._route_colors(config, 7)

    assert colors[:5] == list(config.effective_route_palette)
    assert colors[5:] == list(config.effective_route_palette[:2])
    assert poster._route_colors(poster.PosterConfig(route_color="#123456"), 3) == [
        "#123456",
        "#123456",
        "#123456",
    ]


def test_palette_colors_are_used_during_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = [
        Route(
            name=f"Stage {index + 1}",
            segments=[[(47.0 + index * 0.02, 10.0), (47.01 + index * 0.02, 10.01)]],
            distance_km=2.0,
            ascent_m=50.0,
        )
        for index in range(2)
    ]

    def fake_basemap(*args: object, **kwargs: object):
        image_path = tmp_path / "palette-basemap.png"
        Image.new("RGB", (600, 800), "#d9dfd5").save(image_path)
        pixels = [world_pixel(point, 10) for route in routes for point in route.points]
        return image_path, (
            min(point[0] for point in pixels) - 10,
            min(point[1] for point in pixels) - 10,
            max(point[0] for point in pixels) + 10,
            max(point[1] for point in pixels) + 10,
        )

    monkeypatch.setattr(poster, "make_basemap", fake_basemap)
    requested_colors: list[str] = []
    original_hex_color = poster.HexColor

    def recording_hex_color(value: str):
        requested_colors.append(value)
        return original_hex_color(value)

    monkeypatch.setattr(poster, "HexColor", recording_hex_color)
    output = tmp_path / "palette.pdf"

    create_poster(
        routes,
        output,
        "pdf",
        tmp_path / "cache",
        PosterOptions(
            paper_size="A5",
            basemap_width=512,
            style_name="cool-minimal",
            route_color_mode="palette",
        ),
    )

    assert output.read_bytes().startswith(b"%PDF")
    assert {"#153F63", "#247B78"} <= set(requested_colors)


@pytest.mark.parametrize(
    ("output_format", "suffix", "signature"),
    (("pdf", ".pdf", b"%PDF"), ("png", ".png", b"\x89PNG")),
)
def test_offline_poster_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_format: str,
    suffix: str,
    signature: bytes,
) -> None:
    route = Route(
        name="Sample stage",
        segments=[[(47.0, 10.0), (47.1, 10.1), (47.2, 10.0)]],
        distance_km=25.0,
        ascent_m=750.0,
    )

    def fake_basemap(*args: object, **kwargs: object):
        image_path = tmp_path / "basemap.png"
        Image.new("RGB", (600, 800), "#d9dfd5").save(image_path)
        pixels = [world_pixel(point, 10) for point in route.points]
        return image_path, (
            min(point[0] for point in pixels) - 10,
            min(point[1] for point in pixels) - 10,
            max(point[0] for point in pixels) + 10,
            max(point[1] for point in pixels) + 10,
        )

    monkeypatch.setattr(poster, "make_basemap", fake_basemap)
    requested_colors: list[str] = []
    original_hex_color = poster.HexColor

    def recording_hex_color(value: str):
        requested_colors.append(value)
        return original_hex_color(value)

    monkeypatch.setattr(poster, "HexColor", recording_hex_color)
    output = tmp_path / f"poster{suffix}"

    create_poster(
        [route],
        output,
        output_format,
        tmp_path / "cache",
        PosterOptions(
            paper_size="A5",
            dpi=72,
            basemap_width=512,
            route_color="#2B6CB0",
        ),
    )

    assert output.read_bytes().startswith(signature)
    assert "#2B6CB0" in requested_colors


def test_offline_custom_size_poster_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = Route(
        name="Custom poster stage",
        segments=[[(47.0, 10.0), (47.1, 10.1), (47.2, 10.0)]],
        distance_km=25.0,
        ascent_m=750.0,
    )

    def fake_basemap(*args: object, **kwargs: object):
        image_path = tmp_path / "custom-basemap.png"
        Image.new("RGB", (800, 600), "#d9dfd5").save(image_path)
        pixels = [world_pixel(point, 10) for point in route.points]
        return image_path, (
            min(point[0] for point in pixels) - 10,
            min(point[1] for point in pixels) - 10,
            max(point[0] for point in pixels) + 10,
            max(point[1] for point in pixels) + 10,
        )

    monkeypatch.setattr(poster, "make_basemap", fake_basemap)
    output = tmp_path / "custom-poster.pdf"

    create_poster(
        [route],
        output,
        "pdf",
        tmp_path / "cache",
        PosterOptions(paper_size="300x400mm", basemap_width=512),
    )

    assert output.read_bytes().startswith(b"%PDF")
