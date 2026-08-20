"""Bounded elevation acquisition and resampling for printable relief models."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from .projection import LocalMetricProjection, MetricBounds

CancelCheck = Callable[[], bool]
DEFAULT_MAX_GRID_CELLS = 1_000_000
DEFAULT_MAX_GRID_AXIS = 4096
DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SOURCE_CELLS = 4_000_000
TERRARIUM_TILE_SIZE = 256
TERRARIUM_MAX_ZOOM = 15
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


class ElevationError(ValueError):
    """Raised when elevation data cannot safely satisfy a request."""


class ElevationCancelled(ElevationError):
    """Raised when the caller cancels elevation work."""


def _check_cancelled(cancelled: CancelCheck | None) -> None:
    if cancelled is not None and cancelled():
        raise ElevationCancelled("elevation request cancelled")


@dataclass(frozen=True)
class ElevationRequest:
    """A regular sample grid over local metric terrain bounds."""

    projection: LocalMetricProjection
    bounds_m: MetricBounds
    columns: int
    rows: int

    def __post_init__(self) -> None:
        if self.columns < 2 or self.rows < 2:
            raise ElevationError("elevation grids require at least two rows and columns")
        if self.columns > DEFAULT_MAX_GRID_AXIS or self.rows > DEFAULT_MAX_GRID_AXIS:
            raise ElevationError(f"elevation grid axes cannot exceed {DEFAULT_MAX_GRID_AXIS}")
        if self.columns * self.rows > DEFAULT_MAX_GRID_CELLS:
            raise ElevationError(f"elevation grid cannot exceed {DEFAULT_MAX_GRID_CELLS:,} cells")

    @classmethod
    def for_model(
        cls,
        projection: LocalMetricProjection,
        bounds_m: MetricBounds,
        *,
        width_mm: float,
        depth_mm: float,
        mesh_pitch_mm: float,
        max_cells: int = DEFAULT_MAX_GRID_CELLS,
    ) -> ElevationRequest:
        if not all(math.isfinite(value) and value > 0 for value in (width_mm, depth_mm)):
            raise ElevationError("model dimensions must be positive and finite")
        if width_mm > 256 or depth_mm > 256:
            raise ElevationError("model width and depth cannot exceed 256 mm")
        if not math.isfinite(mesh_pitch_mm) or mesh_pitch_mm <= 0:
            raise ElevationError("mesh pitch must be positive and finite")
        if max_cells < 4 or max_cells > DEFAULT_MAX_GRID_CELLS:
            raise ElevationError(f"max_cells must be between 4 and {DEFAULT_MAX_GRID_CELLS:,}")
        columns = math.ceil(width_mm / mesh_pitch_mm) + 1
        rows = math.ceil(depth_mm / mesh_pitch_mm) + 1
        if columns * rows > max_cells:
            raise ElevationError(
                f"requested mesh needs {columns * rows:,} cells; limit is {max_cells:,}"
            )
        return cls(projection, bounds_m, columns, rows)

    def metric_point(self, row: int, column: int) -> tuple[float, float]:
        if not 0 <= row < self.rows or not 0 <= column < self.columns:
            raise IndexError("elevation grid index out of range")
        x = self.bounds_m.min_x + self.bounds_m.width * column / (self.columns - 1)
        y = self.bounds_m.min_y + self.bounds_m.height * row / (self.rows - 1)
        return x, y

    def geo_point(self, row: int, column: int) -> tuple[float, float]:
        return self.projection.unproject(self.metric_point(row, column))

    def cache_key(self, provider_key: str) -> str:
        payload = {
            "provider": provider_key,
            "origin": [self.projection.origin_latitude, self.projection.origin_longitude],
            "bounds": [
                self.bounds_m.min_x,
                self.bounds_m.min_y,
                self.bounds_m.max_x,
                self.bounds_m.max_y,
            ],
            "shape": [self.rows, self.columns],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ElevationGrid:
    """Finite elevations in metres, ordered south-to-north by row."""

    values_m: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if len(self.values_m) < 2 or len(self.values_m[0]) < 2:
            raise ElevationError("elevation grids require at least two rows and columns")
        columns = len(self.values_m[0])
        if any(len(row) != columns for row in self.values_m):
            raise ElevationError("elevation grid rows must have equal length")
        if any(not math.isfinite(value) for row in self.values_m for value in row):
            raise ElevationError("elevation grid contains non-finite values")

    @property
    def rows(self) -> int:
        return len(self.values_m)

    @property
    def columns(self) -> int:
        return len(self.values_m[0])

    @property
    def minimum_m(self) -> float:
        return min(min(row) for row in self.values_m)

    @property
    def maximum_m(self) -> float:
        return max(max(row) for row in self.values_m)


@dataclass(frozen=True)
class NormalizedElevation:
    """Terrain heights in model mm plus the original elevation range."""

    values_mm: tuple[tuple[float, ...], ...]
    source_minimum_m: float
    source_maximum_m: float
    relief_height_mm: float


class ElevationProvider(Protocol):
    @property
    def cache_identity(self) -> str: ...

    @property
    def attribution(self) -> str: ...

    def load(
        self, request: ElevationRequest, *, cancelled: CancelCheck | None = None
    ) -> ElevationGrid: ...


@dataclass(frozen=True)
class RegularGeoGrid:
    """A small operator-supplied, regularly spaced geographic elevation grid."""

    south: float
    west: float
    latitude_step: float
    longitude_step: float
    values_m: tuple[tuple[float | None, ...], ...]

    def __post_init__(self) -> None:
        if not -90 <= self.south <= 90 or not -180 <= self.west <= 180:
            raise ElevationError("source grid origin is outside latitude/longitude bounds")
        if self.latitude_step <= 0 or self.longitude_step <= 0:
            raise ElevationError("source grid steps must be positive")
        if len(self.values_m) < 2 or len(self.values_m[0]) < 2:
            raise ElevationError("source grid requires at least two rows and columns")
        columns = len(self.values_m[0])
        if any(len(row) != columns for row in self.values_m):
            raise ElevationError("source grid rows must have equal length")
        if len(self.values_m) * columns > DEFAULT_MAX_SOURCE_CELLS:
            raise ElevationError("source grid is too large")
        if self.south + self.latitude_step * (len(self.values_m) - 1) > 90:
            raise ElevationError("source grid extends beyond the north pole")
        if self.longitude_step * (columns - 1) >= 360:
            raise ElevationError("source grid longitude span must be less than 360 degrees")
        for row in self.values_m:
            for value in row:
                if value is not None and not math.isfinite(value):
                    raise ElevationError("source elevation values must be finite or nodata")

    @property
    def rows(self) -> int:
        return len(self.values_m)

    @property
    def columns(self) -> int:
        return len(self.values_m[0])

    def sample(self, latitude: float, longitude: float) -> float | None:
        row_position = (latitude - self.south) / self.latitude_step
        longitude_offset = (longitude - self.west) % 360.0
        column_position = longitude_offset / self.longitude_step
        if not (-1e-9 <= row_position <= self.rows - 1 + 1e-9):
            return None
        if not (-1e-9 <= column_position <= self.columns - 1 + 1e-9):
            return None
        row_position = min(max(row_position, 0.0), self.rows - 1)
        column_position = min(max(column_position, 0.0), self.columns - 1)
        row0 = min(math.floor(row_position), self.rows - 2)
        column0 = min(math.floor(column_position), self.columns - 2)
        row_fraction = row_position - row0
        column_fraction = column_position - column0
        samples = (
            (self.values_m[row0][column0], (1 - row_fraction) * (1 - column_fraction)),
            (self.values_m[row0][column0 + 1], (1 - row_fraction) * column_fraction),
            (self.values_m[row0 + 1][column0], row_fraction * (1 - column_fraction)),
            (self.values_m[row0 + 1][column0 + 1], row_fraction * column_fraction),
        )
        usable = [(value, weight) for value, weight in samples if value is not None and weight > 0]
        total_weight = sum(weight for _, weight in usable)
        if total_weight == 0:
            return None
        return sum(value * weight for value, weight in usable if value is not None) / total_weight


class RegularGridElevationProvider:
    """Resample an in-memory or JSON-backed regular geographic grid."""

    def __init__(
        self,
        grid: RegularGeoGrid,
        *,
        identity: str = "operator-grid",
        max_nodata_fraction: float = 0.25,
    ) -> None:
        if not 0 <= max_nodata_fraction < 1:
            raise ElevationError("max_nodata_fraction must be at least zero and less than one")
        self.grid = grid
        self.max_nodata_fraction = max_nodata_fraction
        digest = hashlib.sha256(repr(grid).encode("utf-8")).hexdigest()[:16]
        self._cache_identity = f"regular:{identity}:{digest}"

    @property
    def cache_identity(self) -> str:
        return self._cache_identity

    @property
    def attribution(self) -> str:
        return "Operator-supplied elevation grid"

    @classmethod
    def from_json(
        cls, path: Path, *, max_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    ) -> RegularGridElevationProvider:
        """Read a bounded local JSON grid; no GDAL/native dependency is required.

        Format: ``south``, ``west``, ``latitude_step``, ``longitude_step``, and
        a rectangular ``values_m`` array. JSON ``null`` represents nodata.
        """
        try:
            if path.stat().st_size > max_bytes:
                raise ElevationError(f"source elevation file exceeds {max_bytes:,} bytes")
            raw = path.read_bytes()
            document = json.loads(raw)
            grid = RegularGeoGrid(
                south=float(document["south"]),
                west=float(document["west"]),
                latitude_step=float(document["latitude_step"]),
                longitude_step=float(document["longitude_step"]),
                values_m=tuple(
                    tuple(None if value is None else float(value) for value in row)
                    for row in document["values_m"]
                ),
            )
        except ElevationError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ElevationError(f"could not read local elevation grid: {path.name}") from exc
        identity = hashlib.sha256(raw).hexdigest()
        return cls(grid, identity=identity)

    def load(
        self, request: ElevationRequest, *, cancelled: CancelCheck | None = None
    ) -> ElevationGrid:
        sampled: list[list[float | None]] = []
        for row in range(request.rows):
            _check_cancelled(cancelled)
            sampled.append(
                [
                    self.grid.sample(*request.geo_point(row, column))
                    for column in range(request.columns)
                ]
            )
        return ElevationGrid(
            _fill_nodata(
                sampled,
                max_missing_fraction=self.max_nodata_fraction,
                cancelled=cancelled,
            )
        )


TileFetcher = Callable[[str, int], bytes]


class TerrariumElevationProvider:
    """Sample public AWS Terrain Tiles using Mapzen's Terrarium encoding."""

    def __init__(
        self,
        *,
        zoom: int = 12,
        cache_dir: Path | None = None,
        max_tiles: int = 256,
        max_tile_bytes: int = 2 * 1024 * 1024,
        timeout_seconds: float = 20.0,
        fetcher: TileFetcher | None = None,
        url_template: str = TERRARIUM_URL,
    ) -> None:
        if not 0 <= zoom <= TERRARIUM_MAX_ZOOM:
            raise ElevationError(f"Terrarium zoom must be between 0 and {TERRARIUM_MAX_ZOOM}")
        if max_tiles < 1 or max_tile_bytes < 1024 or timeout_seconds <= 0:
            raise ElevationError("Terrarium resource limits must be positive")
        self.zoom = zoom
        self.cache_dir = cache_dir
        self.max_tiles = max_tiles
        self.max_tile_bytes = max_tile_bytes
        self.timeout_seconds = timeout_seconds
        self.fetcher = fetcher or self._download
        self.url_template = url_template

    @property
    def cache_identity(self) -> str:
        endpoint = hashlib.sha256(self.url_template.encode("utf-8")).hexdigest()[:12]
        return f"terrarium:z{self.zoom}:{endpoint}"

    @property
    def attribution(self) -> str:
        return (
            "Mapzen Terrain Tiles on AWS Open Data; regional source attribution: "
            "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
        )

    def _download(self, url: str, limit: int) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "outdoor-maps-plot/0.2"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > limit:
                    raise ElevationError("Terrarium tile exceeds the download byte limit")
                data = response.read(limit + 1)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ElevationError("could not download Terrarium elevation tile") from exc
        if len(data) > limit:
            raise ElevationError("Terrarium tile exceeds the download byte limit")
        return data

    def _tile_bytes(self, x: int, y: int) -> bytes:
        tile_count = 1 << self.zoom
        x %= tile_count
        if not 0 <= y < tile_count:
            raise ElevationError("Terrarium sample falls outside the Web Mercator tile range")
        cache_path = (
            self.cache_dir / "terrarium" / str(self.zoom) / str(x) / f"{y}.png"
            if self.cache_dir is not None
            else None
        )
        if cache_path is not None and cache_path.is_file():
            if cache_path.stat().st_size > self.max_tile_bytes:
                raise ElevationError("cached Terrarium tile exceeds the byte limit")
            return cache_path.read_bytes()
        url = self.url_template.format(z=self.zoom, x=x, y=y)
        data = self.fetcher(url, self.max_tile_bytes)
        if not data or len(data) > self.max_tile_bytes:
            raise ElevationError("Terrarium tile is empty or exceeds the byte limit")
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=cache_path.parent, delete=False) as temporary:
                    temporary.write(data)
                    temporary_name = temporary.name
                os.replace(temporary_name, cache_path)
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
        return data

    def load(
        self, request: ElevationRequest, *, cancelled: CancelCheck | None = None
    ) -> ElevationGrid:
        tiles: dict[tuple[int, int], Image.Image] = {}

        def pixel(global_x: int, global_y: int) -> float:
            tile_count = 1 << self.zoom
            world_pixels = tile_count * TERRARIUM_TILE_SIZE
            global_x %= world_pixels
            global_y = min(max(global_y, 0), world_pixels - 1)
            tile_key = (global_x // TERRARIUM_TILE_SIZE, global_y // TERRARIUM_TILE_SIZE)
            if tile_key not in tiles:
                _check_cancelled(cancelled)
                if len(tiles) >= self.max_tiles:
                    raise ElevationError(
                        f"Terrarium request exceeds the {self.max_tiles} tile limit"
                    )
                raw = self._tile_bytes(*tile_key)
                try:
                    with Image.open(io.BytesIO(raw)) as source:
                        if source.size != (TERRARIUM_TILE_SIZE, TERRARIUM_TILE_SIZE):
                            raise ElevationError("Terrarium tile has unexpected dimensions")
                        tiles[tile_key] = source.convert("RGB")
                except (OSError, UnidentifiedImageError) as exc:
                    raise ElevationError("Terrarium tile is not a valid PNG image") from exc
            red, green, blue = tiles[tile_key].getpixel(
                (global_x % TERRARIUM_TILE_SIZE, global_y % TERRARIUM_TILE_SIZE)
            )
            return red * 256.0 + green + blue / 256.0 - 32768.0

        values: list[tuple[float, ...]] = []
        world_pixels = (1 << self.zoom) * TERRARIUM_TILE_SIZE
        for row in range(request.rows):
            _check_cancelled(cancelled)
            output_row: list[float] = []
            for column in range(request.columns):
                latitude, longitude = request.geo_point(row, column)
                latitude = min(max(latitude, -85.05112878), 85.05112878)
                x = (longitude + 180.0) / 360.0 * world_pixels
                latitude_radians = math.radians(latitude)
                y = (1.0 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2.0 * world_pixels
                x0, y0 = math.floor(x), math.floor(y)
                fx, fy = x - x0, y - y0
                output_row.append(
                    pixel(x0, y0) * (1 - fx) * (1 - fy)
                    + pixel(x0 + 1, y0) * fx * (1 - fy)
                    + pixel(x0, y0 + 1) * (1 - fx) * fy
                    + pixel(x0 + 1, y0 + 1) * fx * fy
                )
            values.append(tuple(output_row))
        return ElevationGrid(tuple(values))


def _fill_nodata(
    values: list[list[float | None]],
    *,
    max_missing_fraction: float,
    cancelled: CancelCheck | None = None,
) -> tuple[tuple[float, ...], ...]:
    """Fill nodata from the nearest valid grid cell using Manhattan distance."""
    if not values or not values[0] or any(len(row) != len(values[0]) for row in values):
        raise ElevationError("nodata input must be a non-empty rectangular grid")
    rows, columns = len(values), len(values[0])
    queue: deque[tuple[int, int]] = deque()
    output = [row[:] for row in values]
    missing = 0
    for row in range(rows):
        for column in range(columns):
            value = output[row][column]
            if value is not None and math.isfinite(value):
                queue.append((row, column))
            else:
                output[row][column] = None
                missing += 1
    if not queue:
        raise ElevationError("elevation source contains no usable data for these bounds")
    if missing / (rows * columns) > max_missing_fraction:
        raise ElevationError(
            "elevation source has too much nodata for the requested bounds "
            f"({missing}/{rows * columns} cells)"
        )
    while queue:
        _check_cancelled(cancelled)
        row, column = queue.popleft()
        for next_row, next_column in (
            (row - 1, column),
            (row, column - 1),
            (row, column + 1),
            (row + 1, column),
        ):
            if (
                0 <= next_row < rows
                and 0 <= next_column < columns
                and output[next_row][next_column] is None
            ):
                output[next_row][next_column] = output[row][column]
                queue.append((next_row, next_column))
    return tuple(tuple(float(value) for value in row) for row in output)


def normalize_elevation(grid: ElevationGrid, *, relief_height_mm: float) -> NormalizedElevation:
    """Normalize absolute elevations to a zero-based physical relief height."""
    if not math.isfinite(relief_height_mm) or relief_height_mm < 0:
        raise ElevationError("relief height must be non-negative and finite")
    minimum = grid.minimum_m
    maximum = grid.maximum_m
    span = maximum - minimum
    if span == 0 or relief_height_mm == 0:
        values = tuple(tuple(0.0 for _ in row) for row in grid.values_m)
    else:
        values = tuple(
            tuple((value - minimum) / span * relief_height_mm for value in row)
            for row in grid.values_m
        )
    return NormalizedElevation(values, minimum, maximum, relief_height_mm)
