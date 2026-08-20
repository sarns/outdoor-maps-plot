"""Command-line interface for four-color 3D relief models."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from outdoor_maps_plot import __version__
from outdoor_maps_plot.gpx import GpxError, collect_routes
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.relief_service import ReliefError


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


def _render_relief(*args, **kwargs):
    """Load the geometry pipeline only after CLI arguments have been validated."""
    from outdoor_maps_plot.relief_service import render_relief

    return render_relief(*args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a four-color, print-ready 3D relief model from GPX or FIT tracks.",
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
        default=Path("output/relief.3mf"),
        help="destination 3MF file",
    )
    parser.add_argument(
        "--width-mm",
        type=_positive_float("width"),
        default=240.0,
        help="maximum model width (up to 256 mm)",
    )
    parser.add_argument(
        "--depth-mm",
        type=_positive_float("depth"),
        default=240.0,
        help="maximum model depth (up to 256 mm)",
    )
    parser.add_argument(
        "--base-thickness-mm",
        type=_positive_float("base thickness"),
        default=2.4,
    )
    parser.add_argument(
        "--relief-height-mm",
        type=_positive_float("relief height"),
        default=18.0,
    )
    parser.add_argument(
        "--track-width-mm",
        type=_positive_float("track width"),
        default=1.6,
    )
    parser.add_argument(
        "--track-height-mm",
        type=_positive_float("track height"),
        default=0.8,
    )
    parser.add_argument("--water-height-mm", type=_positive_float("water height"), default=0.4)
    parser.add_argument("--waterway-width-mm", type=_positive_float("waterway width"), default=1.2)
    parser.add_argument(
        "--terrain-split-percent",
        type=_positive_float("terrain split"),
        default=50.0,
        help="height percentage separating green low terrain from brown high terrain",
    )
    parser.add_argument(
        "--mesh-pitch-mm",
        type=_positive_float("mesh pitch"),
        default=0.8,
    )
    parser.add_argument(
        "--padding",
        dest="padding_percent",
        type=_positive_float("padding", allow_zero=True),
        default=6.0,
        metavar="PERCENT",
        help="padding around all tracks",
    )
    parser.add_argument("--terrain-low-color", default="#4D6B50", metavar="#RRGGBB")
    parser.add_argument("--terrain-high-color", default="#8B5A2B", metavar="#RRGGBB")
    parser.add_argument("--water-color", default="#2F75B5", metavar="#RRGGBB")
    parser.add_argument("--track-color", default="#E4431B", metavar="#RRGGBB")
    parser.add_argument(
        "--route-order",
        choices=("auto", "input"),
        default="auto",
        help="auto joins nearest endpoints; input preserves file/track order",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/outdoor-maps-plot"),
        help="downloaded elevation data cache",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = ReliefConfig(
            width_mm=args.width_mm,
            depth_mm=args.depth_mm,
            base_thickness_mm=args.base_thickness_mm,
            relief_height_mm=args.relief_height_mm,
            track_width_mm=args.track_width_mm,
            track_height_mm=args.track_height_mm,
            water_height_mm=args.water_height_mm,
            waterway_width_mm=args.waterway_width_mm,
            mesh_pitch_mm=args.mesh_pitch_mm,
            padding_percent=args.padding_percent,
            terrain_split_percent=args.terrain_split_percent,
            low_color=args.terrain_low_color,
            high_color=args.terrain_high_color,
            water_color=args.water_color,
            track_color=args.track_color,
        )
        routes = collect_routes(args.folder.resolve(), args.route_order)
        output = args.output.with_suffix(".3mf").resolve()
        _render_relief(routes, output, args.cache.resolve(), config)
    except (GpxError, ReliefError, ValidationError) as exc:
        parser.error(str(exc))
    if config.width_mm == 256 or config.depth_mm == 256:
        print("Warning: 256 mm uses the full build dimension and leaves no brim clearance.")
    print(f"Created {output}")


if __name__ == "__main__":
    main()
