"""Command-line interface for outdoor-maps-plot."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from outdoor_maps_plot import __version__
from outdoor_maps_plot.gpx import GpxError, collect_routes
from outdoor_maps_plot.options import PosterConfig
from outdoor_maps_plot.poster import (
    OUTPUT_FORMATS,
    PAGE_SIZES,
    PosterError,
    resolve_output,
)
from outdoor_maps_plot.service import render_poster
from outdoor_maps_plot.styles import PROVIDERS, STYLES


def _bounded_int(minimum: int, maximum: int, label: str):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        return number

    return parse


def _positive_float(label: str, *, allow_zero: bool = False):
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be a number") from exc
        invalid = number < 0 if allow_zero else number <= 0
        if invalid:
            comparison = "zero or greater" if allow_zero else "greater than zero"
            raise argparse.ArgumentTypeError(f"{label} must be {comparison}")
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a print-ready topographic poster from every GPX or FIT track in a folder."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        default=Path("data"),
        help="folder searched recursively for GPX and FIT files",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output/poster.pdf"),
        help="destination; its extension selects PDF, PNG, or JPEG unless --format is set",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=tuple(OUTPUT_FORMATS),
        help="output file type (also normalizes the output extension)",
    )
    parser.add_argument("--title", default="My GPX Adventure", help="poster heading")
    parser.add_argument("--subtitle", default="", help="poster subheading")
    parser.add_argument(
        "--paper-size",
        default="A3",
        metavar="SIZE",
        help="A0-A5, Letter, Legal, Tabloid, or custom WIDTHxHEIGHTmm/cm/in",
    )
    parser.add_argument(
        "--orientation",
        choices=("landscape", "portrait"),
        default="landscape",
    )
    parser.add_argument(
        "--style",
        choices=tuple(STYLES),
        default="classic",
        help="poster color and basemap treatment",
    )
    parser.add_argument(
        "--tile-provider",
        choices=PROVIDERS,
        help="override the map provider selected by the style",
    )
    parser.add_argument(
        "--zoom",
        type=_bounded_int(0, 19, "zoom"),
        default=10,
        help="slippy-map zoom level; higher values use more tiles",
    )
    parser.add_argument(
        "--padding",
        type=_positive_float("padding", allow_zero=True),
        default=6.0,
        metavar="PERCENT",
        help="map padding around all tracks",
    )
    parser.add_argument(
        "--margin-mm",
        type=_positive_float("margin"),
        default=14.8,
        help="poster page margin in millimetres",
    )
    parser.add_argument(
        "--route-width",
        type=_positive_float("route width"),
        default=3.5,
        metavar="POINTS",
        help="route stroke width on an A3 poster",
    )
    parser.add_argument(
        "--route-color",
        metavar="#RRGGBB",
        help="override the track color selected by the style",
    )
    parser.add_argument(
        "--simplify",
        type=_positive_float("simplification tolerance", allow_zero=True),
        default=0.35,
        metavar="POINTS",
        help="visual line simplification tolerance; zero preserves every route point",
    )
    parser.add_argument(
        "--route-order",
        choices=("auto", "input"),
        default="auto",
        help="auto joins nearest endpoints; input preserves file/track order",
    )
    parser.add_argument(
        "--basemap-width",
        type=_bounded_int(512, 10000, "basemap width"),
        default=2400,
        metavar="PIXELS",
        help="minimum embedded basemap width",
    )
    parser.add_argument(
        "--max-tiles",
        type=_bounded_int(1, 500, "tile limit"),
        default=200,
        help="safety limit for map tile downloads",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/outdoor-maps-plot"),
        help="downloaded tile and processed basemap cache",
    )
    parser.add_argument(
        "--dpi",
        type=_bounded_int(72, 1200, "DPI"),
        default=300,
        help="PNG/JPEG resolution; ignored for PDF",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=_bounded_int(1, 100, "JPEG quality"),
        default=92,
        help="JPEG encoder quality",
    )
    parser.add_argument(
        "--list-styles",
        action="store_true",
        help="show style names and exit",
    )
    parser.add_argument(
        "--list-paper-sizes",
        action="store_true",
        help="show named paper sizes and exit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_styles:
        for name, style in STYLES.items():
            print(f"{name:<27} {style.label} (provider: {style.provider})")
        return
    if args.list_paper_sizes:
        print(", ".join(PAGE_SIZES))
        print("Custom example: 300x400mm")
        return

    try:
        output, output_format = resolve_output(args.output, args.output_format)
        config = PosterConfig(
            title=args.title,
            subtitle=args.subtitle,
            paper_size=args.paper_size,
            orientation=args.orientation,
            style_name=args.style,
            provider=args.tile_provider,
            zoom=args.zoom,
            padding_percent=args.padding,
            margin_mm=args.margin_mm,
            basemap_width=args.basemap_width,
            max_tiles=args.max_tiles,
            simplify_points=args.simplify,
            route_width=args.route_width,
            route_color=args.route_color,
            route_order=args.route_order,
            output_format=output_format,
            dpi=args.dpi,
            jpeg_quality=args.jpeg_quality,
        )
        routes = collect_routes(args.folder.resolve(), args.route_order)
        render_poster(
            routes,
            output.resolve(),
            args.cache.resolve(),
            config,
        )
    except (GpxError, PosterError, ValidationError) as exc:
        parser.error(str(exc))
    print(f"Created {output.resolve()}")


if __name__ == "__main__":
    main()
