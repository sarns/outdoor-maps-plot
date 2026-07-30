"""GPX parsing and route ordering."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

Point = tuple[float, float]
TrackPoint = tuple[float, float, float | None]
DEFAULT_MAX_POINTS_PER_FILE = 1_000_000
DEFAULT_MAX_POINTS_TOTAL = 1_000_000


class GpxError(ValueError):
    """Raised when GPX input cannot be used."""


@dataclass
class PointBudget:
    """A shared point allowance for parsing a collection of GPX files."""

    maximum: int = DEFAULT_MAX_POINTS_TOTAL
    used: int = 0

    def __post_init__(self) -> None:
        if self.maximum < 1:
            raise ValueError("maximum point count must be at least one")
        if not 0 <= self.used <= self.maximum:
            raise ValueError("used point count must be within the configured maximum")

    @property
    def remaining(self) -> int:
        return self.maximum - self.used

    def consume(self, source: Path) -> None:
        if self.used >= self.maximum:
            raise GpxError(f"{source.name}: aggregate GPX point limit of {self.maximum:,} exceeded")
        self.used += 1


@dataclass
class Route:
    """One GPX track or route and its calculated statistics."""

    name: str
    segments: list[list[Point]]
    distance_km: float
    ascent_m: float

    @property
    def points(self) -> list[Point]:
        return [point for segment in self.segments for point in segment]

    @property
    def start(self) -> Point:
        return self.segments[0][0]

    @property
    def end(self) -> Point:
        return self.segments[-1][-1]

    def reverse(self) -> None:
        self.segments = [list(reversed(segment)) for segment in reversed(self.segments)]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def _text(element: ET.Element, name: str) -> str:
    child = _direct_child(element, name)
    return child.text.strip() if child is not None and child.text else ""


def _read_points(
    container: ET.Element,
    point_tag: str,
    path: Path,
    *,
    max_points: int,
    parsed_points: list[int],
    point_budget: PointBudget | None,
) -> list[TrackPoint]:
    points: list[TrackPoint] = []
    for element in container:
        if _local_name(element.tag) != point_tag:
            continue
        if parsed_points[0] >= max_points:
            raise GpxError(f"{path.name}: GPX point limit of {max_points:,} exceeded")
        if point_budget is not None:
            point_budget.consume(path)
        parsed_points[0] += 1
        try:
            lat = float(element.attrib["lat"])
            lon = float(element.attrib["lon"])
        except (KeyError, ValueError) as exc:
            raise GpxError(f"{path.name}: invalid latitude or longitude") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise GpxError(f"{path.name}: coordinate outside the valid latitude/longitude range")
        elevation_text = _text(element, "ele")
        try:
            elevation = float(elevation_text) if elevation_text else None
        except ValueError as exc:
            raise GpxError(f"{path.name}: invalid elevation") from exc
        points.append((lat, lon, elevation))
    return points


def _haversine_km(first: TrackPoint, second: TrackPoint) -> float:
    lat1, lon1, _ = first
    lat2, lon2, _ = second
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = lat2_rad - lat1_rad
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _statistics(segments: list[list[TrackPoint]]) -> tuple[float, float]:
    distance_km = 0.0
    ascent_m = 0.0
    for segment in segments:
        for first, second in zip(segment, segment[1:], strict=False):
            distance_km += _haversine_km(first, second)
            if first[2] is not None and second[2] is not None:
                ascent_m += max(0.0, second[2] - first[2])
    return distance_km, ascent_m


def _build_route(name: str, segments: list[list[TrackPoint]]) -> Route | None:
    usable = [segment for segment in segments if len(segment) >= 2]
    if not usable:
        return None
    distance_km, ascent_m = _statistics(usable)
    coordinates = [[(lat, lon) for lat, lon, _ in segment] for segment in usable]
    return Route(name=name, segments=coordinates, distance_km=distance_km, ascent_m=ascent_m)


def parse_gpx(
    path: Path,
    *,
    max_points: int = DEFAULT_MAX_POINTS_PER_FILE,
    point_budget: PointBudget | None = None,
) -> list[Route]:
    """Safely parse GPX tracks and routes within explicit point limits.

    ``max_points`` is the allowance for this file. Pass the same
    :class:`PointBudget` to multiple calls to enforce an aggregate upload limit.
    """
    if max_points < 1:
        raise ValueError("max_points must be at least one")
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError, DefusedXmlException) as exc:
        raise GpxError(f"Could not parse {path.name} as safe XML") from exc
    if _local_name(root.tag) != "gpx":
        raise GpxError(f"{path.name}: root element must be GPX")

    routes: list[Route] = []
    parsed_points = [0]
    for element in root:
        kind = _local_name(element.tag)
        if kind == "trk":
            raw_segments = [
                _read_points(
                    child,
                    "trkpt",
                    path,
                    max_points=max_points,
                    parsed_points=parsed_points,
                    point_budget=point_budget,
                )
                for child in element
                if _local_name(child.tag) == "trkseg"
            ]
        elif kind == "rte":
            raw_segments = [
                _read_points(
                    element,
                    "rtept",
                    path,
                    max_points=max_points,
                    parsed_points=parsed_points,
                    point_budget=point_budget,
                )
            ]
        else:
            continue
        fallback = path.stem if not routes else f"{path.stem} {len(routes) + 1}"
        route = _build_route(_text(element, "name") or fallback, raw_segments)
        if route is not None:
            routes.append(route)
    if not routes:
        raise GpxError(f"{path.name}: no usable GPX tracks or routes found")
    return routes


def _endpoint_distance(first: Point, second: Point) -> float:
    mean_latitude = math.radians((first[0] + second[0]) / 2)
    return math.hypot(first[0] - second[0], (first[1] - second[1]) * math.cos(mean_latitude))


def order_routes(routes: list[Route], mode: str) -> list[Route]:
    """Return input order or a north-to-south nearest-endpoint itinerary."""
    if mode == "input" or len(routes) < 2:
        return routes

    unused = routes[:]
    northern = max(unused, key=lambda route: max(route.start[0], route.end[0]))
    if northern.end[0] > northern.start[0]:
        northern.reverse()
    ordered = [northern]
    unused.remove(northern)

    while unused:
        current = ordered[-1].end
        route, should_reverse, _ = min(
            (
                (route, endpoint == route.end, _endpoint_distance(current, endpoint))
                for route in unused
                for endpoint in (route.start, route.end)
            ),
            key=lambda item: item[2],
        )
        if should_reverse:
            route.reverse()
        ordered.append(route)
        unused.remove(route)
    return ordered


def collect_routes(
    folder: Path,
    order: str = "auto",
    *,
    max_points_per_file: int = DEFAULT_MAX_POINTS_PER_FILE,
    max_points_total: int = DEFAULT_MAX_POINTS_TOTAL,
) -> list[Route]:
    """Read every GPX file below a folder."""
    if not folder.is_dir():
        raise GpxError(f"GPX input folder does not exist: {folder}")
    budget = PointBudget(max_points_total)
    routes = [
        route
        for path in sorted(folder.rglob("*.gpx"))
        for route in parse_gpx(
            path,
            max_points=max_points_per_file,
            point_budget=budget,
        )
    ]
    if not routes:
        raise GpxError(f"No usable GPX tracks found under {folder}")
    return order_routes(routes, order)
