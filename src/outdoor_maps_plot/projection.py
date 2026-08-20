"""Local metric projection and physical model coordinate helpers.

The projection is an ellipsoidal local tangent-plane approximation centred on
the route.  It deliberately avoids using Web Mercator for physical distances.
It is intended for route-sized areas, not for continent-scale cartography.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

GeoPoint = tuple[float, float]
XYPoint = tuple[float, float]

WGS84_SEMI_MAJOR_M = 6_378_137.0
WGS84_ECCENTRICITY_SQUARED = 6.69437999014e-3
MAX_MODEL_DIMENSION_MM = 256.0


def _validate_geo_point(point: GeoPoint) -> None:
    latitude, longitude = point
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("route coordinates must be finite")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("route coordinate outside the valid latitude/longitude range")


def _wrapped_longitude_delta(longitude: float, origin: float) -> float:
    return (longitude - origin + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class LocalMetricProjection:
    """A WGS84 local tangent plane with x=east and y=north, in metres."""

    origin_latitude: float
    origin_longitude: float

    def __post_init__(self) -> None:
        _validate_geo_point((self.origin_latitude, self.origin_longitude))
        if abs(self.origin_latitude) >= 89.999:
            raise ValueError("a local metric projection cannot be centred at a pole")

    @classmethod
    def centered_on(cls, points: Iterable[GeoPoint]) -> LocalMetricProjection:
        coordinates = list(points)
        if not coordinates:
            raise ValueError("at least one route coordinate is required")
        for point in coordinates:
            _validate_geo_point(point)

        latitude = (
            min(point[0] for point in coordinates) + max(point[0] for point in coordinates)
        ) / 2
        # Unwrap relative to the first point, so routes crossing the date line
        # remain compact instead of acquiring an almost-global bounding box.
        anchor = coordinates[0][1]
        unwrapped = [anchor + _wrapped_longitude_delta(point[1], anchor) for point in coordinates]
        longitude = (min(unwrapped) + max(unwrapped)) / 2
        longitude = (longitude + 180.0) % 360.0 - 180.0
        return cls(latitude, longitude)

    @property
    def _metres_per_radian(self) -> tuple[float, float]:
        latitude = math.radians(self.origin_latitude)
        sin_squared = math.sin(latitude) ** 2
        denominator = math.sqrt(1.0 - WGS84_ECCENTRICITY_SQUARED * sin_squared)
        prime_vertical = WGS84_SEMI_MAJOR_M / denominator
        meridional = WGS84_SEMI_MAJOR_M * (1.0 - WGS84_ECCENTRICITY_SQUARED) / denominator**3
        return prime_vertical * math.cos(latitude), meridional

    def project(self, point: GeoPoint) -> XYPoint:
        """Project ``(latitude, longitude)`` to local east/north metres."""
        _validate_geo_point(point)
        east_radius, north_radius = self._metres_per_radian
        latitude, longitude = point
        x = math.radians(_wrapped_longitude_delta(longitude, self.origin_longitude)) * east_radius
        y = math.radians(latitude - self.origin_latitude) * north_radius
        return x, y

    def unproject(self, point: XYPoint) -> GeoPoint:
        """Convert local east/north metres back to latitude/longitude."""
        x, y = point
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("projected coordinates must be finite")
        east_radius, north_radius = self._metres_per_radian
        latitude = self.origin_latitude + math.degrees(y / north_radius)
        longitude = self.origin_longitude + math.degrees(x / east_radius)
        longitude = (longitude + 180.0) % 360.0 - 180.0
        _validate_geo_point((latitude, longitude))
        return latitude, longitude


@dataclass(frozen=True)
class MetricBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value) for value in (self.min_x, self.min_y, self.max_x, self.max_y)
        ):
            raise ValueError("bounds must be finite")
        if self.max_x <= self.min_x or self.max_y <= self.min_y:
            raise ValueError("bounds must have positive width and height")

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y


@dataclass(frozen=True)
class ModelRoute:
    """Projected route, fitted terrain bounds, and route coordinates in mm."""

    projection: LocalMetricProjection
    terrain_bounds_m: MetricBounds
    segments_m: tuple[tuple[XYPoint, ...], ...]
    segments_mm: tuple[tuple[XYPoint, ...], ...]
    width_mm: float
    depth_mm: float


def project_segments(
    segments: Iterable[Iterable[GeoPoint]], projection: LocalMetricProjection
) -> tuple[tuple[XYPoint, ...], ...]:
    """Project route segments while preserving their boundaries."""
    return tuple(tuple(projection.project(point) for point in segment) for segment in segments)


def fit_metric_bounds(
    points: Iterable[XYPoint],
    *,
    target_aspect: float,
    padding_fraction: float = 0.06,
    minimum_span_m: float = 1.0,
) -> MetricBounds:
    """Pad route bounds and expand them to an exact target aspect ratio.

    ``padding_fraction`` is applied on every side.  Expansion for the aspect
    ratio is symmetric, so no coordinate is stretched relative to another.
    """
    coordinates = list(points)
    if not coordinates:
        raise ValueError("at least one projected coordinate is required")
    if not math.isfinite(target_aspect) or target_aspect <= 0:
        raise ValueError("target_aspect must be positive and finite")
    if not math.isfinite(padding_fraction) or padding_fraction < 0:
        raise ValueError("padding_fraction must be non-negative and finite")
    if not math.isfinite(minimum_span_m) or minimum_span_m <= 0:
        raise ValueError("minimum_span_m must be positive and finite")
    if any(not math.isfinite(value) for point in coordinates for value in point):
        raise ValueError("projected coordinates must be finite")

    min_x = min(point[0] for point in coordinates)
    max_x = max(point[0] for point in coordinates)
    min_y = min(point[1] for point in coordinates)
    max_y = max(point[1] for point in coordinates)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    width = max(max_x - min_x, minimum_span_m) * (1 + 2 * padding_fraction)
    height = max(max_y - min_y, minimum_span_m) * (1 + 2 * padding_fraction)

    if width / height < target_aspect:
        width = height * target_aspect
    else:
        height = width / target_aspect
    return MetricBounds(
        center_x - width / 2,
        center_y - height / 2,
        center_x + width / 2,
        center_y + height / 2,
    )


def map_segments_to_model(
    segments_m: Sequence[Sequence[XYPoint]],
    bounds: MetricBounds,
    *,
    width_mm: float,
    depth_mm: float,
) -> tuple[tuple[XYPoint, ...], ...]:
    """Map local metres into the model rectangle in millimetres."""
    _validate_model_size(width_mm, depth_mm)
    return tuple(
        tuple(
            (
                (x - bounds.min_x) / bounds.width * width_mm,
                (y - bounds.min_y) / bounds.height * depth_mm,
            )
            for x, y in segment
        )
        for segment in segments_m
    )


def prepare_model_route(
    segments: Iterable[Iterable[GeoPoint]],
    *,
    width_mm: float,
    depth_mm: float,
    padding_fraction: float = 0.06,
) -> ModelRoute:
    """Project, aspect-fit, and map geographic route segments in one call."""
    _validate_model_size(width_mm, depth_mm)
    materialized = tuple(tuple(segment) for segment in segments)
    points = [point for segment in materialized for point in segment]
    projection = LocalMetricProjection.centered_on(points)
    projected = project_segments(materialized, projection)
    projected_points = [point for segment in projected for point in segment]
    bounds = fit_metric_bounds(
        projected_points,
        target_aspect=width_mm / depth_mm,
        padding_fraction=padding_fraction,
    )
    model_segments = map_segments_to_model(projected, bounds, width_mm=width_mm, depth_mm=depth_mm)
    return ModelRoute(
        projection=projection,
        terrain_bounds_m=bounds,
        segments_m=projected,
        segments_mm=model_segments,
        width_mm=width_mm,
        depth_mm=depth_mm,
    )


def _validate_model_size(width_mm: float, depth_mm: float) -> None:
    if not all(math.isfinite(value) and value > 0 for value in (width_mm, depth_mm)):
        raise ValueError("model width and depth must be positive and finite")
    if width_mm > MAX_MODEL_DIMENSION_MM or depth_mm > MAX_MODEL_DIMENSION_MM:
        raise ValueError(f"model width and depth cannot exceed {MAX_MODEL_DIMENSION_MM:g} mm")
