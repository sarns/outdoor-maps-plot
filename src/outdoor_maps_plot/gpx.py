"""GPX parsing and route ordering."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

Point = tuple[float, float]
TrackPoint = tuple[float, float, float | None]


class GpxError(ValueError):
    """Raised when GPX input cannot be used."""


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


def _read_points(container: ET.Element, point_tag: str, path: Path) -> list[TrackPoint]:
    points: list[TrackPoint] = []
    for element in container:
        if _local_name(element.tag) != point_tag:
            continue
        try:
            lat = float(element.attrib["lat"])
            lon = float(element.attrib["lon"])
        except (KeyError, ValueError) as exc:
            raise GpxError(f"{path}: invalid latitude or longitude") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise GpxError(f"{path}: coordinate outside the valid latitude/longitude range")
        elevation_text = _text(element, "ele")
        try:
            elevation = float(elevation_text) if elevation_text else None
        except ValueError as exc:
            raise GpxError(f"{path}: invalid elevation {elevation_text!r}") from exc
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


def parse_gpx(path: Path) -> list[Route]:
    """Parse GPX tracks and routes without requiring a GPX-specific dependency."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise GpxError(f"Could not read {path}: {exc}") from exc

    routes: list[Route] = []
    for element in root:
        kind = _local_name(element.tag)
        if kind == "trk":
            raw_segments = [
                _read_points(child, "trkpt", path)
                for child in element
                if _local_name(child.tag) == "trkseg"
            ]
        elif kind == "rte":
            raw_segments = [_read_points(element, "rtept", path)]
        else:
            continue
        fallback = path.stem if not routes else f"{path.stem} {len(routes) + 1}"
        route = _build_route(_text(element, "name") or fallback, raw_segments)
        if route is not None:
            routes.append(route)
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


def collect_routes(folder: Path, order: str = "auto") -> list[Route]:
    """Read every GPX file below a folder."""
    if not folder.is_dir():
        raise GpxError(f"GPX input folder does not exist: {folder}")
    routes = [route for path in sorted(folder.rglob("*.gpx")) for route in parse_gpx(path)]
    if not routes:
        raise GpxError(f"No usable GPX tracks found under {folder}")
    return order_routes(routes, order)
