"""Bounded OpenStreetMap water geometry for printable relief maps."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from outdoor_maps_plot.projection import LocalMetricProjection, MetricBounds

Point2D = tuple[float, float]
CancelCheck = Callable[[], bool]
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


class WaterError(ValueError):
    """Raised when mapped water data cannot be loaded safely."""


class WaterCancelled(WaterError):
    """Raised when water loading is cancelled."""


@dataclass(frozen=True, slots=True)
class WaterArea:
    outer_mm: tuple[Point2D, ...]
    holes_mm: tuple[tuple[Point2D, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class WaterLine:
    points_mm: tuple[Point2D, ...]
    width_mm: float


@dataclass(frozen=True, slots=True)
class WaterFeatures:
    areas: tuple[WaterArea, ...] = ()
    lines: tuple[WaterLine, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.areas and not self.lines


class WaterProvider(Protocol):
    @property
    def cache_identity(self) -> str: ...

    @property
    def attribution(self) -> str: ...

    def load(
        self,
        projection: LocalMetricProjection,
        bounds: MetricBounds,
        *,
        width_mm: float,
        depth_mm: float,
        minimum_line_width_mm: float,
        cancelled: CancelCheck | None = None,
    ) -> WaterFeatures: ...


WaterFetcher = Callable[[str, bytes, int], bytes]


class OpenStreetMapWaterProvider:
    """Load lakes and flowing waterways through the public Overpass API."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        endpoint: str = OVERPASS_URL,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 16 * 1024 * 1024,
        max_coordinates: int = 250_000,
        fetcher: WaterFetcher | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes < 1024 or max_coordinates < 10:
            raise WaterError("water provider resource limits must be positive")
        self.cache_dir = cache_dir
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_coordinates = max_coordinates
        self.fetcher = fetcher or self._download

    @property
    def cache_identity(self) -> str:
        digest = hashlib.sha256(self.endpoint.encode("utf-8")).hexdigest()[:12]
        return f"osm-water:{digest}"

    @property
    def attribution(self) -> str:
        return "Water geometry © OpenStreetMap contributors, ODbL 1.0"

    def _download(self, url: str, body: bytes, limit: int) -> bytes:
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "outdoor-maps-plot/0.2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > limit:
                    raise WaterError("OpenStreetMap water response exceeds the byte limit")
                payload = response.read(limit + 1)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise WaterError("could not download OpenStreetMap water geometry") from exc
        if len(payload) > limit:
            raise WaterError("OpenStreetMap water response exceeds the byte limit")
        return payload

    def load(
        self,
        projection: LocalMetricProjection,
        bounds: MetricBounds,
        *,
        width_mm: float,
        depth_mm: float,
        minimum_line_width_mm: float,
        cancelled: CancelCheck | None = None,
    ) -> WaterFeatures:
        _check_cancelled(cancelled)
        south, west, north, east = _geo_bounds(projection, bounds)
        query = _query(south, west, north, east)
        cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json" if self.cache_dir else None
        if cache_path is not None and cache_path.is_file():
            if cache_path.stat().st_size > self.max_response_bytes:
                raise WaterError("cached OpenStreetMap water response exceeds the byte limit")
            payload = cache_path.read_bytes()
        else:
            body = urllib.parse.urlencode({"data": query}).encode("ascii")
            payload = self.fetcher(self.endpoint, body, self.max_response_bytes)
            if not payload or len(payload) > self.max_response_bytes:
                raise WaterError("OpenStreetMap water response is empty or too large")
            if cache_path is not None:
                _atomic_cache_write(cache_path, payload)
        _check_cancelled(cancelled)
        return _parse(
            payload,
            projection,
            bounds,
            width_mm,
            depth_mm,
            minimum_line_width_mm,
            self.max_coordinates,
            cancelled,
        )


def _query(south: float, west: float, north: float, east: float) -> str:
    bbox = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    return (
        "[out:json][timeout:25];("
        f'way["natural"="water"]({bbox});relation["natural"="water"]({bbox});'
        f'way["waterway"="riverbank"]({bbox});'
        f'relation["waterway"="riverbank"]({bbox});'
        f'way["landuse"="reservoir"]({bbox});way["landuse"="basin"]({bbox});'
        f'way["waterway"="river"]({bbox});way["waterway"="stream"]({bbox});'
        f'way["waterway"="canal"]({bbox});way["waterway"="ditch"]({bbox});'
        f'way["waterway"="drain"]({bbox});'
        ");out geom;"
    )


def _geo_bounds(
    projection: LocalMetricProjection, bounds: MetricBounds
) -> tuple[float, float, float, float]:
    corners = [
        projection.unproject((x, y))
        for x in (bounds.min_x, bounds.max_x)
        for y in (bounds.min_y, bounds.max_y)
    ]
    origin = projection.origin_longitude
    unwrapped = [origin + (point[1] - origin + 180.0) % 360.0 - 180.0 for point in corners]
    west = (min(unwrapped) + 180.0) % 360.0 - 180.0
    east = (max(unwrapped) + 180.0) % 360.0 - 180.0
    return (
        min(point[0] for point in corners),
        west,
        max(point[0] for point in corners),
        east,
    )


def _parse(
    payload: bytes,
    projection: LocalMetricProjection,
    bounds: MetricBounds,
    width_mm: float,
    depth_mm: float,
    minimum_line_width_mm: float,
    max_coordinates: int,
    cancelled: CancelCheck | None,
) -> WaterFeatures:
    try:
        document = json.loads(payload)
        elements = document["elements"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WaterError("OpenStreetMap water response is not valid Overpass JSON") from exc
    if not isinstance(elements, list):
        raise WaterError("OpenStreetMap water response has an invalid element list")

    areas: list[WaterArea] = []
    lines: list[WaterLine] = []
    coordinate_count = 0
    scale = width_mm / bounds.width

    def model_points(raw: object) -> tuple[Point2D, ...]:
        nonlocal coordinate_count
        if not isinstance(raw, list):
            return ()
        coordinate_count += len(raw)
        if coordinate_count > max_coordinates:
            raise WaterError("OpenStreetMap water geometry exceeds the coordinate limit")
        result: list[Point2D] = []
        for point in raw:
            if not isinstance(point, dict) or "lat" not in point or "lon" not in point:
                continue
            x, y = projection.project((float(point["lat"]), float(point["lon"])))
            result.append(
                (
                    (x - bounds.min_x) / bounds.width * width_mm,
                    (y - bounds.min_y) / bounds.height * depth_mm,
                )
            )
        return tuple(result)

    for element in elements:
        _check_cancelled(cancelled)
        if not isinstance(element, dict):
            continue
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        if element.get("type") == "relation":
            outer_segments: list[tuple[Point2D, ...]] = []
            inner_segments: list[tuple[Point2D, ...]] = []
            for member in element.get("members", []):
                if not isinstance(member, dict) or member.get("type") != "way":
                    continue
                points = model_points(member.get("geometry"))
                if len(points) >= 2:
                    (inner_segments if member.get("role") == "inner" else outer_segments).append(
                        points
                    )
            holes = tuple(_stitch_closed(inner_segments))
            for outer in _stitch_closed(outer_segments):
                matching = tuple(hole for hole in holes if _point_in_ring(hole[0], outer))
                areas.append(WaterArea(outer, matching))
            continue

        points = model_points(element.get("geometry"))
        if len(points) < 2:
            continue
        waterway = tags.get("waterway")
        is_area = points[0] == points[-1] and (
            tags.get("natural") == "water"
            or waterway == "riverbank"
            or tags.get("landuse") in {"reservoir", "basin"}
        )
        if is_area and len(points) >= 4:
            areas.append(WaterArea(points))
        elif waterway in {"river", "stream", "canal", "ditch", "drain"}:
            width_m = _numeric_width(tags.get("width"))
            lines.append(WaterLine(points, max(minimum_line_width_mm, width_m * scale)))
    return WaterFeatures(tuple(areas), tuple(lines))


def _numeric_width(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        number = float(value.strip())
    except ValueError:
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _stitch_closed(segments: list[tuple[Point2D, ...]]) -> list[tuple[Point2D, ...]]:
    remaining = [list(segment) for segment in segments]
    rings: list[tuple[Point2D, ...]] = []
    while remaining:
        ring = remaining.pop()
        changed = True
        while changed and not _same_point(ring[0], ring[-1]):
            changed = False
            for index, segment in enumerate(remaining):
                if _same_point(ring[-1], segment[0]):
                    ring.extend(segment[1:])
                elif _same_point(ring[-1], segment[-1]):
                    ring.extend(reversed(segment[:-1]))
                elif _same_point(ring[0], segment[-1]):
                    ring[:0] = segment[:-1]
                elif _same_point(ring[0], segment[0]):
                    ring[:0] = list(reversed(segment[1:]))
                else:
                    continue
                remaining.pop(index)
                changed = True
                break
        if len(ring) >= 4 and _same_point(ring[0], ring[-1]):
            ring[-1] = ring[0]
            rings.append(tuple(ring))
    return rings


def _same_point(first: Point2D, second: Point2D) -> bool:
    return math.dist(first, second) <= 1e-5


def _point_in_ring(point: Point2D, ring: tuple[Point2D, ...]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, ring[1:], strict=False):
        if (first[1] > y) != (second[1] > y):
            intersection = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1])
            if x < first[0] + intersection:
                inside = not inside
    return inside


def _check_cancelled(cancelled: CancelCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise WaterCancelled("water loading cancelled")


def _atomic_cache_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
