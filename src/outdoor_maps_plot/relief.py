"""Build printable relief meshes from a rectangular elevation grid.

The module deliberately has no DEM or GPX dependency.  Its coordinates are
millimetres in model space: the elevation grid covers ``0..width_mm`` and
``0..depth_mm``, and route polylines use that same coordinate system.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.water import WaterArea, WaterFeatures

Point2D = tuple[float, float]
Vertex = tuple[float, float, float]
Triangle = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Mesh:
    """One named, single-material triangle mesh."""

    name: str
    color: str
    vertices: tuple[Vertex, ...]
    triangles: tuple[Triangle, ...]


@dataclass(frozen=True, slots=True)
class ReliefModel:
    """A complete four-material relief ready for validation and export."""

    bodies: tuple[Mesh, ...]
    width_mm: float
    depth_mm: float
    height_mm: float
    source_elevation_range: tuple[float, float]
    band_heights_mm: tuple[float, ...]


class _MeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[Vertex] = []
        self.triangles: list[Triangle] = []
        self._indices: dict[Vertex, int] = {}

    def vertex(self, value: Vertex) -> int:
        # Plane intersections reached from adjacent cells must weld exactly.
        key = tuple(round(float(component), 9) for component in value)
        if key not in self._indices:
            self._indices[key] = len(self.vertices)
            self.vertices.append(key)
        return self._indices[key]

    def triangle(self, a: Vertex, b: Vertex, c: Vertex) -> None:
        cross = _cross(_subtract(b, a), _subtract(c, a))
        if _length(cross) <= 1e-10:
            return
        self.triangles.append((self.vertex(a), self.vertex(b), self.vertex(c)))

    def mesh(self, name: str, color: str) -> Mesh:
        return Mesh(name, color, tuple(self.vertices), tuple(self.triangles))


def build_relief_model(
    elevations: Sequence[Sequence[float]],
    route_lines_mm: Sequence[Sequence[Point2D]],
    config: ReliefConfig | None = None,
    water_features: WaterFeatures | None = None,
) -> ReliefModel:
    """Create two terrain bands, mapped water, and one draped track body.

    Elevations are linearly normalized into ``relief_height_mm``.  A constant
    grid is represented as a plateau at the full relief height, which keeps all
    required terrain materials printable. Terrain is split at the configured
    percentage of relief height and the two bodies share a non-overlapping
    interface. Water is raised slightly above the terrain surface.

    Route ribbons are robust for ordinary non-self-intersecting polylines.
    Crossing/overlapping route ribbons are not boolean-unioned in this bounded
    MVP; callers should simplify such tracks before production export.
    """

    config = config or ReliefConfig()
    grid = _validate_grid(elevations)
    rows, columns = len(grid), len(grid[0])
    source_min = min(map(min, grid))
    source_max = max(map(max, grid))

    if math.isclose(source_min, source_max, rel_tol=0, abs_tol=1e-12):
        surface = [
            [config.base_thickness_mm + config.relief_height_mm] * columns for _ in range(rows)
        ]
    else:
        scale = config.relief_height_mm / (source_max - source_min)
        surface = [
            [config.base_thickness_mm + (value - source_min) * scale for value in row]
            for row in grid
        ]

    requested_split_height = config.base_thickness_mm + config.relief_height_mm * (
        config.terrain_split_percent / 100
    )
    split_height = _nudge_height_off_surface_vertices(requested_split_height, surface)
    surface_triangles = _surface_triangles(surface, config.width_mm, config.depth_mm)

    low = _build_terrain_band(surface_triangles, 0.0, split_height, "terrain-low", config.low_color)
    high = _build_terrain_band(
        surface_triangles, split_height, None, "terrain-high", config.high_color
    )
    water_mask = _water_mask(
        water_features or WaterFeatures(),
        rows,
        columns,
        config.width_mm,
        config.depth_mm,
    )
    water = _build_water(
        surface,
        water_mask,
        config.width_mm,
        config.depth_mm,
        config.water_height_mm,
        config.water_color,
    )
    track = _build_track(
        surface,
        route_lines_mm,
        config.width_mm,
        config.depth_mm,
        config.track_width_mm,
        config.track_height_mm,
        config.mesh_pitch_mm,
        config.track_color,
        water_mask,
        config.water_height_mm,
    )

    bodies = (low, high, track) if water is None else (low, high, water, track)

    model = ReliefModel(
        bodies=bodies,
        width_mm=config.width_mm,
        depth_mm=config.depth_mm,
        height_mm=(
            config.base_thickness_mm
            + config.relief_height_mm
            + max(config.track_height_mm + config.water_height_mm, config.water_height_mm)
        ),
        source_elevation_range=(source_min, source_max),
        band_heights_mm=(split_height,),
    )
    # Keep invalid artifacts from ever reaching an exporter.
    from outdoor_maps_plot.mesh_validation import validate_relief_model

    validate_relief_model(model)
    return model


def _validate_grid(elevations: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(elevations) < 2:
        raise ValueError("elevation grid must contain at least two rows")
    width = len(elevations[0])
    if width < 2:
        raise ValueError("elevation grid must contain at least two columns")
    grid: list[list[float]] = []
    for row in elevations:
        if len(row) != width:
            raise ValueError("elevation grid must be rectangular")
        converted = [float(value) for value in row]
        if not all(math.isfinite(value) for value in converted):
            raise ValueError("elevation grid may contain only finite values")
        grid.append(converted)
    return grid


def _surface_triangles(
    surface: Sequence[Sequence[float]], width: float, depth: float
) -> list[tuple[Vertex, Vertex, Vertex]]:
    rows, columns = len(surface), len(surface[0])
    result: list[tuple[Vertex, Vertex, Vertex]] = []
    for row in range(rows - 1):
        y0, y1 = depth * row / (rows - 1), depth * (row + 1) / (rows - 1)
        for column in range(columns - 1):
            x0 = width * column / (columns - 1)
            x1 = width * (column + 1) / (columns - 1)
            v00 = (x0, y0, surface[row][column])
            v10 = (x1, y0, surface[row][column + 1])
            v11 = (x1, y1, surface[row + 1][column + 1])
            v01 = (x0, y1, surface[row + 1][column])
            result.extend(((v00, v10, v11), (v00, v11, v01)))
    return result


def _nudge_height_off_surface_vertices(
    requested: float, surface: Sequence[Sequence[float]]
) -> float:
    """Avoid zero-thickness terrain pinches where a band hits grid vertices exactly."""

    heights = tuple(value for row in surface for value in row)
    if not any(math.isclose(value, requested, rel_tol=0, abs_tol=1e-9) for value in heights):
        return requested
    minimum, maximum = min(heights), max(heights)
    step = 1e-6
    for multiplier in range(1, 1001):
        for direction in (1.0, -1.0):
            candidate = requested + direction * multiplier * step
            if not minimum < candidate < maximum:
                continue
            if not any(
                math.isclose(value, candidate, rel_tol=0, abs_tol=1e-9) for value in heights
            ):
                return candidate
    raise ValueError("could not place terrain color split between elevation samples")


def _build_terrain_band(
    source_triangles: Sequence[tuple[Vertex, Vertex, Vertex]],
    lower: float,
    upper: float | None,
    name: str,
    color: str,
) -> Mesh:
    builder = _MeshBuilder()
    boundary_edges: dict[tuple[Point2D, Point2D], list[tuple[Vertex, Vertex]]] = defaultdict(list)

    for triangle in source_triangles:
        polygon = _clip_above(list(triangle), lower)
        if len(polygon) < 3:
            continue
        top = [(x, y, min(z, upper) if upper is not None else z) for x, y, z in polygon]
        bottom = [(x, y, lower) for x, y, _ in polygon]
        for index in range(1, len(top) - 1):
            builder.triangle(top[0], top[index], top[index + 1])
            builder.triangle(bottom[0], bottom[index + 1], bottom[index])
        for index, start in enumerate(top):
            end = top[(index + 1) % len(top)]
            key = _undirected_xy_key(start, end)
            boundary_edges[key].append((start, end))

    for occurrences in boundary_edges.values():
        if len(occurrences) != 1:
            continue
        top_a, top_b = occurrences[0]
        bottom_a = (top_a[0], top_a[1], lower)
        bottom_b = (top_b[0], top_b[1], lower)
        builder.triangle(top_a, bottom_a, bottom_b)
        builder.triangle(top_a, bottom_b, top_b)

    return builder.mesh(name, color)


def _clip_above(polygon: list[Vertex], height: float) -> list[Vertex]:
    output: list[Vertex] = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        current_inside = current[2] >= height
        previous_inside = previous[2] >= height
        if current_inside != previous_inside:
            ratio = (height - previous[2]) / (current[2] - previous[2])
            output.append(
                (
                    previous[0] + ratio * (current[0] - previous[0]),
                    previous[1] + ratio * (current[1] - previous[1]),
                    height,
                )
            )
        if current_inside:
            output.append(current)
    deduplicated: list[Vertex] = []
    for vertex in output:
        if not deduplicated or not _same_vertex(vertex, deduplicated[-1]):
            deduplicated.append(vertex)
    if len(deduplicated) > 1 and _same_vertex(deduplicated[0], deduplicated[-1]):
        deduplicated.pop()
    return deduplicated


def _same_vertex(first: Vertex, second: Vertex) -> bool:
    return all(
        math.isclose(a, b, rel_tol=0, abs_tol=1e-9) for a, b in zip(first, second, strict=True)
    )


def _undirected_xy_key(a: Vertex, b: Vertex) -> tuple[Point2D, Point2D]:
    first = (round(a[0], 9), round(a[1], 9))
    second = (round(b[0], 9), round(b[1], 9))
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def _build_track(
    surface: Sequence[Sequence[float]],
    route_lines: Sequence[Sequence[Point2D]],
    width: float,
    depth: float,
    track_width: float,
    track_height: float,
    mesh_pitch: float,
    color: str,
    water_mask: Sequence[Sequence[bool]],
    water_height: float,
) -> Mesh:
    builder = _MeshBuilder()
    half_width = track_width / 2
    usable_lines = 0
    for raw_line in route_lines:
        line = _resample_line(raw_line, mesh_pitch / 2)
        if len(line) < 2:
            continue
        sections: list[tuple[Vertex, Vertex, Vertex, Vertex]] = []
        for index, point in enumerate(line):
            previous = line[max(0, index - 1)]
            following = line[min(len(line) - 1, index + 1)]
            dx, dy = following[0] - previous[0], following[1] - previous[1]
            magnitude = math.hypot(dx, dy)
            if magnitude <= 1e-12:
                continue
            nx, ny = -dy / magnitude * half_width, dx / magnitude * half_width
            left = (point[0] + nx, point[1] + ny)
            right = (point[0] - nx, point[1] - ny)
            for x, y in (left, right):
                if x < 0 or x > width or y < 0 or y > depth:
                    raise ValueError("route plus half its track width must fit inside the model")
            left_bottom_z = _sample_surface(surface, left[0], left[1], width, depth)
            right_bottom_z = _sample_surface(surface, right[0], right[1], width, depth)
            if _sample_mask(water_mask, left[0], left[1], width, depth):
                left_bottom_z += water_height
            if _sample_mask(water_mask, right[0], right[1], width, depth):
                right_bottom_z += water_height
            sections.append(
                (
                    (left[0], left[1], left_bottom_z),
                    (right[0], right[1], right_bottom_z),
                    (left[0], left[1], left_bottom_z + track_height),
                    (right[0], right[1], right_bottom_z + track_height),
                )
            )
        if len(sections) < 2:
            continue
        usable_lines += 1
        _add_track_strip(builder, sections)
    if not usable_lines:
        raise ValueError("at least one route polyline with two distinct points is required")
    return builder.mesh("track", color)


def _water_mask(
    features: WaterFeatures,
    rows: int,
    columns: int,
    width: float,
    depth: float,
) -> list[list[bool]]:
    cell_width = width / (columns - 1)
    cell_depth = depth / (rows - 1)
    line_margin = math.hypot(cell_width, cell_depth) / 2
    mask: list[list[bool]] = []
    for row in range(rows - 1):
        output_row: list[bool] = []
        y = (row + 0.5) * cell_depth
        for column in range(columns - 1):
            x = (column + 0.5) * cell_width
            point = (x, y)
            in_area = any(_point_in_area(point, area) for area in features.areas)
            near_line = any(
                _distance_to_line(point, line.points_mm) <= line.width_mm / 2 + line_margin
                for line in features.lines
            )
            in_raster = False
            if features.raster_mask and features.raster_mask[0]:
                source_row = min(
                    len(features.raster_mask) - 1,
                    math.floor((row + 0.5) / (rows - 1) * len(features.raster_mask)),
                )
                source_column = min(
                    len(features.raster_mask[0]) - 1,
                    math.floor((column + 0.5) / (columns - 1) * len(features.raster_mask[0])),
                )
                in_raster = features.raster_mask[source_row][source_column]
            output_row.append(in_area or near_line or in_raster)
        mask.append(output_row)
    return _repair_diagonal_contacts(mask)


def _repair_diagonal_contacts(mask: list[list[bool]]) -> list[list[bool]]:
    """Bridge corner-only cells so the extruded water remains a 2-manifold."""

    if len(mask) < 2 or len(mask[0]) < 2:
        return mask
    changed = True
    while changed:
        changed = False
        for row in range(len(mask) - 1):
            for column in range(len(mask[0]) - 1):
                top_left = mask[row][column]
                top_right = mask[row][column + 1]
                bottom_left = mask[row + 1][column]
                bottom_right = mask[row + 1][column + 1]
                if top_left and bottom_right and not top_right and not bottom_left:
                    mask[row][column + 1] = True
                    changed = True
                elif top_right and bottom_left and not top_left and not bottom_right:
                    mask[row][column] = True
                    changed = True
    return mask


def _point_in_area(point: Point2D, area: WaterArea) -> bool:
    return _point_in_polygon(point, area.outer_mm) and not any(
        _point_in_polygon(point, hole) for hole in area.holes_mm
    )


def _point_in_polygon(point: Point2D, ring: Sequence[Point2D]) -> bool:
    x, y = point
    inside = False
    for first, second in zip(ring, ring[1:], strict=False):
        if (first[1] > y) != (second[1] > y):
            crossing = first[0] + (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1])
            if x < crossing:
                inside = not inside
    return inside


def _distance_to_line(point: Point2D, line: Sequence[Point2D]) -> float:
    if len(line) < 2:
        return math.inf
    return min(
        _distance_to_segment(point, first, second)
        for first, second in zip(line, line[1:], strict=False)
    )


def _distance_to_segment(point: Point2D, first: Point2D, second: Point2D) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-18:
        return math.dist(point, first)
    ratio = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / denominator
    ratio = min(1.0, max(0.0, ratio))
    return math.dist(point, (first[0] + ratio * dx, first[1] + ratio * dy))


def _build_water(
    surface: Sequence[Sequence[float]],
    mask: Sequence[Sequence[bool]],
    width: float,
    depth: float,
    height: float,
    color: str,
) -> Mesh | None:
    if not any(any(row) for row in mask):
        return None
    rows, columns = len(surface), len(surface[0])
    builder = _MeshBuilder()
    for row in range(rows - 1):
        y0, y1 = depth * row / (rows - 1), depth * (row + 1) / (rows - 1)
        for column in range(columns - 1):
            if not mask[row][column]:
                continue
            x0, x1 = width * column / (columns - 1), width * (column + 1) / (columns - 1)
            b00 = (x0, y0, surface[row][column])
            b10 = (x1, y0, surface[row][column + 1])
            b11 = (x1, y1, surface[row + 1][column + 1])
            b01 = (x0, y1, surface[row + 1][column])
            t00, t10, t11, t01 = ((x, y, z + height) for x, y, z in (b00, b10, b11, b01))
            builder.triangle(t00, t10, t11)
            builder.triangle(t00, t11, t01)
            builder.triangle(b00, b11, b10)
            builder.triangle(b00, b01, b11)
            if row == 0 or not mask[row - 1][column]:
                builder.triangle(b00, b10, t10)
                builder.triangle(b00, t10, t00)
            if row == rows - 2 or not mask[row + 1][column]:
                builder.triangle(b01, t01, t11)
                builder.triangle(b01, t11, b11)
            if column == 0 or not mask[row][column - 1]:
                builder.triangle(b00, t00, t01)
                builder.triangle(b00, t01, b01)
            if column == columns - 2 or not mask[row][column + 1]:
                builder.triangle(b10, b11, t11)
                builder.triangle(b10, t11, t10)
    return builder.mesh("water", color)


def _sample_mask(
    mask: Sequence[Sequence[bool]], x: float, y: float, width: float, depth: float
) -> bool:
    if not mask or not mask[0]:
        return False
    column = min(len(mask[0]) - 1, max(0, int(x / width * len(mask[0]))))
    row = min(len(mask) - 1, max(0, int(y / depth * len(mask))))
    return mask[row][column]


def _resample_line(points: Sequence[Point2D], maximum_step: float) -> list[Point2D]:
    if len(points) < 2:
        return []
    result = [(float(points[0][0]), float(points[0][1]))]
    for raw_end in points[1:]:
        start = result[-1]
        end = (float(raw_end[0]), float(raw_end[1]))
        if not all(math.isfinite(value) for value in end):
            raise ValueError("route coordinates must be finite")
        distance = math.dist(start, end)
        if distance <= 1e-12:
            continue
        steps = max(1, math.ceil(distance / maximum_step))
        result.extend(
            (
                start[0] + (end[0] - start[0]) * step / steps,
                start[1] + (end[1] - start[1]) * step / steps,
            )
            for step in range(1, steps + 1)
        )
    return result


def _sample_surface(
    surface: Sequence[Sequence[float]], x: float, y: float, width: float, depth: float
) -> float:
    rows, columns = len(surface), len(surface[0])
    gx = min(columns - 1, max(0.0, x / width * (columns - 1)))
    gy = min(rows - 1, max(0.0, y / depth * (rows - 1)))
    column = min(columns - 2, int(gx))
    row = min(rows - 2, int(gy))
    tx, ty = gx - column, gy - row
    v00 = surface[row][column]
    v10 = surface[row][column + 1]
    v11 = surface[row + 1][column + 1]
    v01 = surface[row + 1][column]
    # Match the v00-v11 diagonal used by _surface_triangles.
    if ty <= tx:
        return v00 * (1 - tx) + v10 * (tx - ty) + v11 * ty
    return v00 * (1 - ty) + v11 * tx + v01 * (ty - tx)


def _add_track_strip(
    builder: _MeshBuilder, sections: Sequence[tuple[Vertex, Vertex, Vertex, Vertex]]
) -> None:
    for current, following in zip(sections, sections[1:], strict=False):
        lb0, rb0, lt0, rt0 = current
        lb1, rb1, lt1, rt1 = following
        builder.triangle(lt0, rt0, rt1)
        builder.triangle(lt0, rt1, lt1)
        builder.triangle(lb0, rb1, rb0)
        builder.triangle(lb0, lb1, rb1)
        builder.triangle(rb0, rb1, rt1)
        builder.triangle(rb0, rt1, rt0)
        builder.triangle(lb0, lt1, lb1)
        builder.triangle(lb0, lt0, lt1)

    lb0, rb0, lt0, rt0 = sections[0]
    builder.triangle(lb0, rb0, rt0)
    builder.triangle(lb0, rt0, lt0)
    lb1, rb1, lt1, rt1 = sections[-1]
    builder.triangle(lb1, rt1, rb1)
    builder.triangle(lb1, lt1, rt1)


def _subtract(a: Vertex, b: Vertex) -> Vertex:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _cross(a: Vertex, b: Vertex) -> Vertex:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(value: Iterable[float]) -> float:
    return math.sqrt(sum(component * component for component in value))
