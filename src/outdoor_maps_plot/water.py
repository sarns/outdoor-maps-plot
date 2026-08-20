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
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from outdoor_maps_plot.projection import LocalMetricProjection, MetricBounds

Point2D = tuple[float, float]
CancelCheck = Callable[[], bool]
WaterProgress = Callable[[int, int], None]
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_ENDPOINTS = (
    OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)


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
    complete: bool = True

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
        minimum_area_mm2: float,
        cancelled: CancelCheck | None = None,
        progress: WaterProgress | None = None,
    ) -> WaterFeatures: ...


WaterFetcher = Callable[[str, bytes, int], bytes]


class OpenStreetMapWaterProvider:
    """Load large lakes and reservoirs through the public Overpass API."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        endpoint: str | None = None,
        endpoints: Sequence[str] | None = None,
        timeout_seconds: float = 6.0,
        total_timeout_seconds: float = 15.0,
        max_response_bytes: int = 64 * 1024 * 1024,
        max_coordinates: int = 500_000,
        max_query_tiles: int = 4,
        target_tile_span_km: float = 100.0,
        max_workers: int = 4,
        max_endpoint_attempts: int = 1,
        fetcher: WaterFetcher | None = None,
    ) -> None:
        if (
            timeout_seconds <= 0
            or total_timeout_seconds <= 0
            or max_response_bytes < 1024
            or max_coordinates < 10
            or not 1 <= max_query_tiles <= 64
            or target_tile_span_km <= 0
            or not 1 <= max_workers <= 8
            or not 1 <= max_endpoint_attempts <= 3
        ):
            raise WaterError("water provider resource limits must be positive")
        if endpoint is not None and endpoints is not None:
            raise WaterError("configure either one water endpoint or an endpoint list")
        if endpoints is not None:
            configured_endpoints = tuple(endpoints)
        elif endpoint is not None:
            configured_endpoints = (endpoint,)
        else:
            configured_endpoints = OVERPASS_ENDPOINTS
        if not configured_endpoints or any(
            not value.startswith("https://") for value in configured_endpoints
        ):
            raise WaterError("water endpoints must be non-empty HTTPS URLs")
        self.cache_dir = cache_dir
        self.endpoints = configured_endpoints
        # Keep the original public attribute for callers that configured one endpoint.
        self.endpoint = configured_endpoints[0]
        self.timeout_seconds = timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_coordinates = max_coordinates
        self.max_query_tiles = max_query_tiles
        self.target_tile_span_km = target_tile_span_km
        self.max_workers = max_workers
        self.max_endpoint_attempts = max_endpoint_attempts
        self.fetcher = fetcher or self._download

    @property
    def cache_identity(self) -> str:
        digest = hashlib.sha256("\n".join(self.endpoints).encode("utf-8")).hexdigest()[:12]
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
        minimum_area_mm2: float,
        cancelled: CancelCheck | None = None,
        progress: WaterProgress | None = None,
    ) -> WaterFeatures:
        _check_cancelled(cancelled)
        if not math.isfinite(minimum_area_mm2) or minimum_area_mm2 <= 0:
            raise WaterError("minimum printed lake area must be positive")
        south, west, north, east = _geo_bounds(projection, bounds)
        queries = tuple(
            _query(*tile)
            for tile in _query_tiles(
                south,
                west,
                north,
                east,
                target_span_km=self.target_tile_span_km,
                max_tiles=self.max_query_tiles,
            )
        )
        areas: list[WaterArea] = []
        bytes_used = 0
        coordinates_used = 0
        completed_queries = 0
        attempted_queries = 0
        per_query_byte_limit = max(1024, self.max_response_bytes // len(queries))
        worker_count = min(self.max_workers, len(queries))
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="osm-water")
        futures = {
            executor.submit(self._load_query, query, per_query_byte_limit): query
            for query in queries
        }
        timed_out = False
        try:
            for future in as_completed(futures, timeout=self.total_timeout_seconds):
                attempted_queries += 1
                if progress is not None:
                    progress(attempted_queries, len(queries))
                _check_cancelled(cancelled)
                try:
                    payload = future.result()
                    remaining_coordinates = self.max_coordinates - coordinates_used
                    if bytes_used + len(payload) > self.max_response_bytes:
                        raise WaterError(
                            "OpenStreetMap water geometry exceeds the total safety limit"
                        )
                    if remaining_coordinates < 10:
                        raise WaterError(
                            "OpenStreetMap water geometry exceeds the total safety limit"
                        )
                    features, coordinate_count = _parse(
                        payload,
                        projection,
                        bounds,
                        width_mm,
                        depth_mm,
                        minimum_area_mm2,
                        remaining_coordinates,
                        cancelled,
                    )
                except WaterCancelled:
                    raise
                except WaterError:
                    continue
                bytes_used += len(payload)
                coordinates_used += coordinate_count
                areas.extend(features.areas)
                completed_queries += 1
        except FuturesTimeoutError:
            timed_out = True
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if completed_queries == 0:
            raise WaterError("could not download OpenStreetMap water geometry for any map tile")
        return WaterFeatures(
            tuple(dict.fromkeys(areas)),
            (),
            complete=not timed_out and completed_queries == len(queries),
        )

    def _load_query(self, query: str, remaining_bytes: int) -> bytes:
        cache_key = hashlib.sha256(query.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json" if self.cache_dir else None
        if cache_path is not None and cache_path.is_file():
            if cache_path.stat().st_size > remaining_bytes:
                raise WaterError("cached OpenStreetMap water response exceeds the byte limit")
            return cache_path.read_bytes()
        body = urllib.parse.urlencode({"data": query}).encode("ascii")
        payload = self._fetch_from_available_endpoint(body, remaining_bytes)
        if cache_path is not None:
            _atomic_cache_write(cache_path, payload)
        return payload

    def _fetch_from_available_endpoint(self, body: bytes, limit: int) -> bytes:
        last_error: WaterError | None = None
        start_index = int.from_bytes(hashlib.sha256(body).digest()[:2], "big") % len(self.endpoints)
        attempt_count = min(self.max_endpoint_attempts, len(self.endpoints))
        for offset in range(attempt_count):
            endpoint = self.endpoints[(start_index + offset) % len(self.endpoints)]
            try:
                payload = self.fetcher(endpoint, body, limit)
                if not payload or len(payload) > limit:
                    raise WaterError("OpenStreetMap water response is empty or too large")
                return payload
            except WaterError as exc:
                last_error = exc
        raise WaterError(
            "could not download OpenStreetMap water geometry from any public endpoint"
        ) from last_error


def _query(
    south: float,
    west: float,
    north: float,
    east: float,
) -> str:
    bbox = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    return (
        "[out:json][timeout:6];("
        f'way["natural"="water"]["water"~"^(lake|reservoir)$"]({bbox});'
        f'relation["natural"="water"]["water"~"^(lake|reservoir)$"]({bbox});'
        f'way["landuse"="reservoir"]({bbox});'
        f'relation["landuse"="reservoir"]({bbox});'
        ");out geom qt;"
    )


def _query_tiles(
    south: float,
    west: float,
    north: float,
    east: float,
    *,
    target_span_km: float,
    max_tiles: int,
) -> tuple[tuple[float, float, float, float], ...]:
    latitude_km = max((north - south) * 111.32, 0.001)
    middle_latitude = (south + north) / 2
    longitude_km = max(
        (east - west) * 111.32 * max(math.cos(math.radians(middle_latitude)), 0.05),
        0.001,
    )
    rows = max(1, math.ceil(latitude_km / target_span_km))
    columns = max(1, math.ceil(longitude_km / target_span_km))
    while rows * columns > max_tiles:
        if rows >= columns and rows > 1:
            rows -= 1
        elif columns > 1:
            columns -= 1
        else:
            break
    latitude_step = (north - south) / rows
    longitude_step = (east - west) / columns
    return tuple(
        (
            south + row * latitude_step,
            west + column * longitude_step,
            north if row == rows - 1 else south + (row + 1) * latitude_step,
            east if column == columns - 1 else west + (column + 1) * longitude_step,
        )
        for row in range(rows)
        for column in range(columns)
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
    minimum_area_mm2: float,
    max_coordinates: int,
    cancelled: CancelCheck | None,
) -> tuple[WaterFeatures, int]:
    try:
        document = json.loads(payload)
        elements = document["elements"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WaterError("OpenStreetMap water response is not valid Overpass JSON") from exc
    if not isinstance(elements, list):
        raise WaterError("OpenStreetMap water response has an invalid element list")

    areas: list[WaterArea] = []
    coordinate_count = 0

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
                area = WaterArea(outer, matching)
                if _water_area_mm2(area) >= minimum_area_mm2:
                    areas.append(area)
            continue

        points = model_points(element.get("geometry"))
        if len(points) < 2:
            continue
        is_area = points[0] == points[-1] and (
            tags.get("natural") == "water" or tags.get("landuse") == "reservoir"
        )
        if is_area and len(points) >= 4:
            area = WaterArea(points)
            if _water_area_mm2(area) >= minimum_area_mm2:
                areas.append(area)
    return WaterFeatures(tuple(areas)), coordinate_count


def _water_area_mm2(area: WaterArea) -> float:
    return max(
        0.0,
        abs(_signed_ring_area(area.outer_mm))
        - sum(abs(_signed_ring_area(hole)) for hole in area.holes_mm),
    )


def _signed_ring_area(ring: tuple[Point2D, ...]) -> float:
    return (
        sum(
            first[0] * second[1] - second[0] * first[1]
            for first, second in zip(ring, ring[1:], strict=False)
        )
        / 2
    )


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
