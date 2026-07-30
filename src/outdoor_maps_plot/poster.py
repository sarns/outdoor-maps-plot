"""Topographic basemap assembly and poster rendering."""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A0, A1, A2, A3, A4, A5, LEGAL, LETTER, TABLOID
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from outdoor_maps_plot.gpx import Point, Route
from outdoor_maps_plot.options import PosterConfig
from outdoor_maps_plot.styles import ATTRIBUTIONS, STYLES, Style

PAGE_SIZES = {
    "A0": A0,
    "A1": A1,
    "A2": A2,
    "A3": A3,
    "A4": A4,
    "A5": A5,
    "LETTER": LETTER,
    "LEGAL": LEGAL,
    "TABLOID": TABLOID,
}
OUTPUT_FORMATS = {"pdf": ".pdf", "png": ".png", "jpeg": ".jpg", "jpg": ".jpg"}
RenderPhaseName = Literal["fetching_map", "drawing", "rasterizing"]
RenderProgress = Callable[[RenderPhaseName, int, str], None]
CancellationCheck = Callable[[], None]


class PosterError(ValueError):
    """Raised when a poster cannot be generated."""


@dataclass(frozen=True)
class PosterOptions:
    """Legacy CLI adapter; new integrations should construct :class:`PosterConfig`."""

    title: str = "My GPX Adventure"
    subtitle: str = ""
    paper_size: str = "A3"
    orientation: str = "landscape"
    style_name: str = "classic"
    provider: str | None = None
    zoom: int = 10
    padding: float = 0.06
    margin_mm: float = 14.8
    basemap_width: int = 2400
    max_tiles: int = 200
    simplify_points: float = 0.35
    route_width: float = 3.5
    route_color: str | None = None
    dpi: int = 300
    jpeg_quality: int = 92

    def to_config(self, output_format: str = "pdf") -> PosterConfig:
        return PosterConfig(
            title=self.title,
            subtitle=self.subtitle,
            paper_size=self.paper_size,
            orientation=self.orientation,
            style_name=self.style_name,
            provider=self.provider,
            zoom=self.zoom,
            padding_percent=self.padding * 100,
            margin_mm=self.margin_mm,
            basemap_width=self.basemap_width,
            max_tiles=self.max_tiles,
            simplify_points=self.simplify_points,
            route_width=self.route_width,
            route_color=self.route_color,
            output_format="jpeg" if output_format == "jpg" else output_format,
            dpi=self.dpi,
            jpeg_quality=self.jpeg_quality,
        )


def _emit_progress(
    progress: RenderProgress | None,
    phase: RenderPhaseName,
    percent: int,
    message: str,
) -> None:
    if progress is not None:
        progress(phase, percent, message)


def _check_cancellation(check: CancellationCheck | None) -> None:
    if check is not None:
        check()


def parse_page_size(value: str) -> tuple[float, float]:
    """Parse a named paper size or WIDTHxHEIGHT with mm, cm, or in units."""
    normalized = value.strip().upper()
    if normalized in PAGE_SIZES:
        return PAGE_SIZES[normalized]
    match = re.fullmatch(
        r"(?P<width>\d+(?:\.\d+)?)\s*[X×]\s*(?P<height>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>MM|CM|IN)",
        normalized,
    )
    if not match:
        choices = ", ".join(PAGE_SIZES)
        raise PosterError(
            f"Invalid paper size {value!r}; use {choices}, or a custom size such as 300x400mm"
        )
    width = float(match.group("width"))
    height = float(match.group("height"))
    if width <= 0 or height <= 0:
        raise PosterError("Paper dimensions must be greater than zero")
    unit = {"MM": mm, "CM": 10 * mm, "IN": inch}[match.group("unit")]
    return width * unit, height * unit


def oriented_page_size(value: str, orientation: str) -> tuple[float, float]:
    width, height = parse_page_size(value)
    if orientation == "landscape":
        return max(width, height), min(width, height)
    return min(width, height), max(width, height)


def resolve_output(path: Path, explicit_format: str | None) -> tuple[Path, str]:
    """Resolve an output path and format, using the extension when possible."""
    if explicit_format:
        output_format = "jpeg" if explicit_format == "jpg" else explicit_format
        return path.with_suffix(OUTPUT_FORMATS[output_format]), output_format
    suffix = path.suffix.lower()
    output_format = next(
        (name for name, extension in OUTPUT_FORMATS.items() if extension == suffix),
        None,
    )
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format is None:
        raise PosterError(
            "Could not infer the output type; use a .pdf, .png, or .jpg extension, or pass --format"
        )
    return path, output_format


def world_pixel(point: Point, zoom: int) -> tuple[float, float]:
    """Convert a latitude/longitude coordinate to a Web Mercator pixel."""
    lat, lon = point
    size = 256 * (2**zoom)
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180) / 360 * size
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * size
    return x, y


def _recolor_basemap(image: Image.Image, style_name: str) -> Image.Image:
    image = image.convert("RGB")
    if style_name == "muted-alpine":
        image = ImageEnhance.Color(image).enhance(0.48)
        image = ImageEnhance.Contrast(image).enhance(0.78)
        return ImageEnhance.Brightness(image).enhance(1.13)
    if style_name == "monochrome-relief":
        image = ImageOps.grayscale(image)
        image = ImageEnhance.Contrast(image).enhance(0.88)
        return ImageEnhance.Brightness(image).enhance(1.12).convert("RGB")
    if style_name == "vintage-expedition":
        gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(0.92)
        return Image.blend(image, ImageOps.colorize(gray, "#4A3827", "#F2E2BD"), 0.78)
    if style_name == "cool-minimal":
        gray = ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(0.76)
        return Image.blend(image, ImageOps.colorize(gray, "#304D57", "#E9F1EE"), 0.87)
    if style_name == "dark-topographic":
        gray = ImageEnhance.Contrast(ImageOps.invert(ImageOps.grayscale(image))).enhance(0.72)
        return ImageOps.colorize(gray, "#071110", "#71817A")
    if style_name == "high-contrast-hiking":
        image = ImageEnhance.Color(image).enhance(1.18)
        image = ImageEnhance.Contrast(image).enhance(1.18)
        return ImageEnhance.Sharpness(image).enhance(1.2)
    return image


def _tile_url(provider: str, zoom: int, tile_x: int, tile_y: int) -> str:
    if provider == "esri":
        return (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            f"World_Topo_Map/MapServer/tile/{zoom}/{tile_y}/{tile_x}"
        )
    if provider == "stadia":
        key = os.environ.get("STADIA_MAPS_API_KEY")
        if not key:
            raise PosterError("Stamen Terrain requires STADIA_MAPS_API_KEY to be set")
        return (
            "https://tiles.stadiamaps.com/tiles/stamen_terrain/"
            f"{zoom}/{tile_x}/{tile_y}.png?api_key={key}"
        )
    if provider == "thunderforest":
        key = os.environ.get("THUNDERFOREST_API_KEY")
        if not key:
            raise PosterError("Thunderforest Outdoors requires THUNDERFOREST_API_KEY to be set")
        return f"https://api.thunderforest.com/outdoors/{zoom}/{tile_x}/{tile_y}.png?apikey={key}"
    subdomain = "abc"[(tile_x + tile_y) % 3]
    return f"https://{subdomain}.tile.opentopomap.org/{zoom}/{tile_x}/{tile_y}.png"


def _fit_bounds(
    points: list[Point], zoom: int, target_aspect: float, padding: float
) -> tuple[float, float, float, float]:
    pixels = [world_pixel(point, zoom) for point in points]
    left, right = min(point[0] for point in pixels), max(point[0] for point in pixels)
    top, bottom = min(point[1] for point in pixels), max(point[1] for point in pixels)
    base_span = max(right - left, bottom - top, 32.0)
    pad = base_span * padding
    left, right, top, bottom = left - pad, right + pad, top - pad, bottom + pad

    current_aspect = (right - left) / (bottom - top)
    if current_aspect < target_aspect:
        extra = ((bottom - top) * target_aspect - (right - left)) / 2
        left, right = left - extra, right + extra
    else:
        extra = ((right - left) / target_aspect - (bottom - top)) / 2
        top, bottom = top - extra, bottom + extra

    world_size = 256 * (2**zoom)
    if left < 0 or right > world_size or top < 0 or bottom > world_size:
        raise PosterError("The route is too close to the Web Mercator map boundary")
    return left, top, right, bottom


def _download_tile(
    url: str,
    path: Path,
    *,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    _check_cancellation(cancellation_check)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "outdoor-maps-plot/0.1 (personal print map)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = bytearray()
            while chunk := response.read(64 * 1024):
                _check_cancellation(cancellation_check)
                data.extend(chunk)
            _check_cancellation(cancellation_check)
            path.write_bytes(data)
    except (OSError, urllib.error.HTTPError):
        raise PosterError(
            "Could not download a map tile. Check the connection, provider "
            "credentials, and tile usage policy."
        ) from None


def make_basemap(
    points: list[Point],
    target_aspect: float,
    cache: Path,
    style_name: str,
    provider: str,
    zoom: int,
    padding: float,
    basemap_width: int,
    max_tiles: int,
    *,
    progress: RenderProgress | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> tuple[Path, tuple[float, float, float, float]]:
    """Download, stitch, crop, and style a tile mosaic around the routes."""
    if provider == "opentopo" and zoom > 17:
        raise PosterError("OpenTopoMap supports zoom levels up to 17")
    bounds = _fit_bounds(points, zoom, target_aspect, padding)
    left, top, right, bottom = bounds
    x0, x1 = math.floor(left / 256), math.floor((right - 1e-9) / 256)
    y0, y1 = math.floor(top / 256), math.floor((bottom - 1e-9) / 256)
    tile_count = (x1 - x0 + 1) * (y1 - y0 + 1)
    if tile_count > max_tiles:
        raise PosterError(
            f"The selected zoom needs {tile_count} map tiles, over the {max_tiles} tile limit. "
            "Choose a lower --zoom or raise --max-tiles deliberately."
        )

    cache.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(
        f"{style_name}|{provider}|{zoom}|{bounds}|{basemap_width}".encode()
    ).hexdigest()[:16]
    output = cache / "basemaps" / f"{cache_key}.png"
    if output.exists():
        _check_cancellation(cancellation_check)
        _emit_progress(progress, "fetching_map", 54, "Topographic map ready")
        return output, bounds

    tile_dir = cache / "tiles" / provider
    mosaic = Image.new(
        "RGB",
        ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256),
        "#e8e5dc",
    )
    completed_tiles = 0
    last_percent = -1
    for tile_y in range(y0, y1 + 1):
        for tile_x in range(x0, x1 + 1):
            _check_cancellation(cancellation_check)
            tile_path = tile_dir / str(zoom) / str(tile_x) / f"{tile_y}.png"
            if not tile_path.exists():
                _download_tile(
                    _tile_url(provider, zoom, tile_x, tile_y),
                    tile_path,
                    cancellation_check=cancellation_check,
                )
            try:
                with Image.open(tile_path) as tile:
                    mosaic.paste(
                        tile.convert("RGB"),
                        ((tile_x - x0) * 256, (tile_y - y0) * 256),
                    )
            except (OSError, UnidentifiedImageError) as exc:
                raise PosterError(
                    "A cached map tile is not a valid image. Clear the tile cache and render again."
                ) from exc
            completed_tiles += 1
            percent = 18 + round(completed_tiles / tile_count * 34)
            if percent != last_percent:
                _emit_progress(progress, "fetching_map", percent, "Preparing topographic map")
                last_percent = percent

    _check_cancellation(cancellation_check)
    crop = (
        round(left - x0 * 256),
        round(top - y0 * 256),
        round(right - x0 * 256),
        round(bottom - y0 * 256),
    )
    basemap = mosaic.crop(crop)
    _check_cancellation(cancellation_check)
    if basemap.width < basemap_width:
        factor = basemap_width / basemap.width
        basemap = basemap.resize(
            (round(basemap.width * factor), round(basemap.height * factor)),
            Image.Resampling.LANCZOS,
        )
    basemap = _recolor_basemap(basemap, style_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    basemap.save(output, optimize=True)
    _emit_progress(progress, "fetching_map", 54, "Topographic map ready")
    return output, bounds


def _point_segment_distance_sq(point: Point, start: Point, end: Point) -> float:
    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
    if delta_x == 0 and delta_y == 0:
        return (point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2
    position = ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y) / (
        delta_x**2 + delta_y**2
    )
    position = max(0.0, min(1.0, position))
    nearest = (start[0] + position * delta_x, start[1] + position * delta_y)
    return (point[0] - nearest[0]) ** 2 + (point[1] - nearest[1]) ** 2


def simplify_line(
    points: list[Point],
    tolerance: float,
    cancellation_check: CancellationCheck | None = None,
) -> list[Point]:
    """Simplify a projected line using iterative Ramer-Douglas-Peucker."""
    if len(points) <= 2 or tolerance <= 0:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    tolerance_sq = tolerance**2
    iterations = 0
    while stack:
        if iterations % 128 == 0:
            _check_cancellation(cancellation_check)
        iterations += 1
        start, end = stack.pop()
        furthest_index = -1
        furthest_distance = 0.0
        for index in range(start + 1, end):
            distance = _point_segment_distance_sq(points[index], points[start], points[end])
            if distance > furthest_distance:
                furthest_index, furthest_distance = index, distance
        if furthest_index >= 0 and furthest_distance > tolerance_sq:
            keep.add(furthest_index)
            stack.extend(((start, furthest_index), (furthest_index, end)))
    return [points[index] for index in sorted(keep)]


def _fit_text_size(
    canvas: Canvas,
    text: str,
    font: str,
    maximum: float,
    minimum: float,
    available_width: float,
) -> float:
    size = maximum
    while size > minimum and canvas.stringWidth(text, font, size) > available_width:
        size -= 0.5
    return size


def _truncate_text(
    canvas: Canvas, text: str, font: str, size: float, available_width: float
) -> str:
    if canvas.stringWidth(text, font, size) <= available_width:
        return text
    shortened = text
    while shortened and canvas.stringWidth(f"{shortened}.", font, size) > available_width:
        shortened = shortened[:-1]
    return f"{shortened}."


def _draw_route_paths(
    canvas: Canvas,
    routes: list[Route],
    project: Callable[[Point], Point],
    tolerance: float,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    for route in routes:
        _check_cancellation(cancellation_check)
        for segment in route.segments:
            _check_cancellation(cancellation_check)
            projected = simplify_line(
                [project(point) for point in segment],
                tolerance,
                cancellation_check,
            )
            path = canvas.beginPath()
            for index, (x, y) in enumerate(projected):
                (path.moveTo if index == 0 else path.lineTo)(x, y)
            canvas.drawPath(path, stroke=1, fill=0)


def _render_pdf(
    routes: list[Route],
    output: Path,
    cache: Path,
    config: PosterConfig,
    *,
    progress: RenderProgress | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    _check_cancellation(cancellation_check)
    if not routes:
        raise PosterError("At least one usable route is required")
    style: Style = STYLES[config.style_name]
    route_color = config.effective_route_color
    provider = config.effective_provider
    page_w, page_h = oriented_page_size(config.paper_size, config.orientation)
    reference_w, reference_h = oriented_page_size("A3", config.orientation)
    scale = min(page_w / reference_w, page_h / reference_h)
    margin = config.margin_mm * mm
    columns = min(len(routes), 4 if config.orientation == "portrait" else 7)
    rows = math.ceil(len(routes) / columns)
    footer_h = max(70 * scale, (19 + rows * 51) * scale)
    header_h = 104 * scale
    map_x, map_y = margin, footer_h + 32 * scale
    map_w, map_h = page_w - 2 * margin, page_h - map_y - header_h
    if map_w <= 0 or map_h <= 0:
        raise PosterError(
            "The page is too small for the selected margin and number of stages; "
            "reduce --margin-mm or choose a larger paper size"
        )

    all_points = [point for route in routes for point in route.points]
    if max(point[1] for point in all_points) - min(point[1] for point in all_points) > 180:
        raise PosterError("Routes crossing the antimeridian are not currently supported")
    basemap, bounds = make_basemap(
        all_points,
        map_w / map_h,
        cache,
        config.style_name,
        provider,
        config.zoom,
        config.padding_percent / 100,
        config.basemap_width,
        config.max_tiles,
        progress=progress,
        cancellation_check=cancellation_check,
    )
    _check_cancellation(cancellation_check)
    _emit_progress(progress, "drawing", 57, "Drawing poster")
    pixel_left, pixel_top, pixel_right, pixel_bottom = bounds

    def project(point: Point) -> Point:
        pixel_x, pixel_y = world_pixel(point, config.zoom)
        return (
            map_x + (pixel_x - pixel_left) / (pixel_right - pixel_left) * map_w,
            map_y + (pixel_bottom - pixel_y) / (pixel_bottom - pixel_top) * map_h,
        )

    canvas = Canvas(str(output), pagesize=(page_w, page_h), pageCompression=1)
    canvas.setTitle(config.title)
    canvas.setAuthor("outdoor-maps-plot")
    canvas.setFillColor(HexColor(style.paper))
    canvas.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    canvas.saveState()
    clip = canvas.beginPath()
    clip.roundRect(map_x, map_y, map_w, map_h, 5 * scale)
    canvas.clipPath(clip, stroke=0)
    canvas.drawImage(
        ImageReader(str(basemap)),
        map_x,
        map_y,
        map_w,
        map_h,
        preserveAspectRatio=False,
        mask="auto",
    )
    canvas.setLineCap(1)
    canvas.setLineJoin(1)
    canvas.setStrokeColor(HexColor(style.halo))
    canvas.setLineWidth((config.route_width + 3.5) * scale)
    _draw_route_paths(
        canvas,
        routes,
        project,
        config.simplify_points * scale,
        cancellation_check,
    )
    canvas.setStrokeColor(HexColor(route_color))
    canvas.setLineWidth(config.route_width * scale)
    _draw_route_paths(
        canvas,
        routes,
        project,
        config.simplify_points * scale,
        cancellation_check,
    )

    for route in routes:
        _check_cancellation(cancellation_check)
        for point in (route.start, route.end):
            x, y = project(point)
            canvas.setFillColor(HexColor(style.halo))
            canvas.circle(x, y, 4.2 * scale, fill=1, stroke=0)
            canvas.setFillColor(HexColor(route_color))
            canvas.circle(x, y, 2.35 * scale, fill=1, stroke=0)
    canvas.restoreState()

    _emit_progress(progress, "drawing", 72, "Adding poster details")
    title = config.title.upper()
    title_size = _fit_text_size(
        canvas, title, "Helvetica-Bold", 31 * scale, 14 * scale, page_w * 0.58
    )
    canvas.setFillColor(HexColor(style.ink))
    canvas.setFont("Helvetica-Bold", title_size)
    canvas.drawString(margin, page_h - 58 * scale, title)
    canvas.setFont("Helvetica", 10 * scale)
    canvas.setFillColor(HexColor(style.muted))
    subtitle = _truncate_text(
        canvas,
        config.subtitle.upper(),
        "Helvetica",
        10 * scale,
        page_w - 2 * margin,
    )
    canvas.drawString(margin + scale, page_h - 77 * scale, subtitle)

    total_km = sum(route.distance_km for route in routes)
    total_ascent = sum(route.ascent_m for route in routes)
    statistics = (
        f"{len(routes):02d} STAGES   /   {total_km:,.0f} KM   /   {total_ascent:,.0f} M ASCENT"
    )
    stat_size = _fit_text_size(
        canvas, statistics, "Helvetica-Bold", 9 * scale, 5 * scale, page_w * 0.39
    )
    canvas.setFont("Helvetica-Bold", stat_size)
    canvas.setFillColor(HexColor(style.route))
    canvas.drawRightString(page_w - margin, page_h - 60 * scale, statistics)
    canvas.setStrokeColor(HexColor(style.ink))
    canvas.setLineWidth(0.8 * scale)
    canvas.line(margin, page_h - 91 * scale, page_w - margin, page_h - 91 * scale)

    canvas.line(margin, footer_h + 16 * scale, page_w - margin, footer_h + 16 * scale)
    column_width = (page_w - 2 * margin) / columns
    for index, route in enumerate(routes):
        _check_cancellation(cancellation_check)
        column, row = index % columns, index // columns
        x = margin + column * column_width
        y = footer_h - 2 * scale - row * 51 * scale
        canvas.setFillColor(HexColor(style.route))
        canvas.setFont("Helvetica-Bold", 8 * scale)
        canvas.drawString(x, y, f"{index + 1:02d}")
        canvas.setFillColor(HexColor(style.ink))
        canvas.setFont("Helvetica-Bold", 7.2 * scale)
        label = _truncate_text(
            canvas,
            route.name.replace("_", " ").strip().upper() or f"STAGE {index + 1}",
            "Helvetica-Bold",
            7.2 * scale,
            column_width - 4 * scale,
        )
        canvas.drawString(x, y - 13 * scale, label)
        canvas.setFillColor(HexColor(style.muted))
        canvas.setFont("Helvetica", 7 * scale)
        canvas.drawString(
            x,
            y - 26 * scale,
            f"{route.distance_km:.1f} KM  /  +{route.ascent_m:.0f} M",
        )

    min_lat = min(point[0] for point in all_points)
    max_lat = max(point[0] for point in all_points)
    min_lon = min(point[1] for point in all_points)
    max_lon = max(point[1] for point in all_points)
    coordinates = f"{min_lat:.2f}–{max_lat:.2f} N  /  {min_lon:.2f}–{max_lon:.2f} E"
    canvas.setFillColor(HexColor(style.muted))
    canvas.setFont("Helvetica", 6.5 * scale)
    canvas.drawString(margin, 22 * scale, coordinates)
    attribution = _truncate_text(
        canvas,
        ATTRIBUTIONS[provider],
        "Helvetica",
        6.5 * scale,
        page_w * 0.62,
    )
    canvas.drawRightString(page_w - margin, 22 * scale, attribution)
    _check_cancellation(cancellation_check)
    canvas.showPage()
    canvas.save()
    _emit_progress(progress, "drawing", 84, "Poster artwork ready")


def create_poster(
    routes: list[Route],
    output: Path,
    output_format: str,
    cache: Path,
    options: PosterConfig | PosterOptions,
    *,
    progress: RenderProgress | None = None,
    cancellation_check: CancellationCheck | None = None,
) -> None:
    """Create a PDF directly or render the PDF design to a raster image."""
    try:
        config = (
            options.to_config(output_format)
            if isinstance(options, PosterOptions)
            else PosterConfig.model_validate(
                {
                    **options.model_dump(),
                    "output_format": "jpeg" if output_format == "jpg" else output_format,
                }
            )
        )
    except ValidationError as exc:
        raise PosterError(f"Invalid poster configuration: {exc}") from exc
    _check_cancellation(cancellation_check)
    output.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    if config.output_format == "pdf":
        _render_pdf(
            routes,
            output,
            cache,
            config,
            progress=progress,
            cancellation_check=cancellation_check,
        )
        return

    with tempfile.TemporaryDirectory(prefix="outdoor-maps-plot-", dir=output.parent) as temp:
        pdf_path = Path(temp) / "poster.pdf"
        _render_pdf(
            routes,
            pdf_path,
            cache,
            config,
            progress=progress,
            cancellation_check=cancellation_check,
        )
        _check_cancellation(cancellation_check)
        _emit_progress(progress, "rasterizing", 86, "Rasterizing poster")
        document = pdfium.PdfDocument(pdf_path)
        try:
            page = document[0]
            try:
                bitmap = page.render(scale=config.dpi / 72)
                try:
                    image = bitmap.to_pil().convert("RGB").copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
        _check_cancellation(cancellation_check)
        save_options: dict[str, object] = {"dpi": (config.dpi, config.dpi)}
        if config.output_format == "jpeg":
            save_options.update(quality=config.jpeg_quality, optimize=True)
        image.save(
            output,
            format="JPEG" if config.output_format == "jpeg" else "PNG",
            **save_options,
        )
        _check_cancellation(cancellation_check)
        _emit_progress(progress, "rasterizing", 94, "Raster poster ready")
