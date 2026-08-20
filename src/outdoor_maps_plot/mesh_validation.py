"""Strict structural checks for generated relief meshes."""

from __future__ import annotations

import math
from collections import Counter

from outdoor_maps_plot.relief import Mesh, ReliefModel, _cross, _length, _subtract


class MeshValidationError(ValueError):
    """Raised when a relief cannot be safely handed to a slicer."""


def validate_mesh(mesh: Mesh) -> None:
    if not mesh.vertices or not mesh.triangles:
        raise MeshValidationError(f"{mesh.name} must be nonempty")
    if not all(math.isfinite(component) for vertex in mesh.vertices for component in vertex):
        raise MeshValidationError(f"{mesh.name} contains a non-finite vertex")

    edge_counts: Counter[tuple[int, int]] = Counter()
    signed_volume = 0.0
    for face in mesh.triangles:
        if len(set(face)) != 3 or any(index < 0 or index >= len(mesh.vertices) for index in face):
            raise MeshValidationError(f"{mesh.name} contains an invalid triangle index")
        a, b, c = (mesh.vertices[index] for index in face)
        if _length(_cross(_subtract(b, a), _subtract(c, a))) <= 1e-10:
            raise MeshValidationError(f"{mesh.name} contains a degenerate triangle")
        edge_counts.update(
            (
                tuple(sorted((face[0], face[1]))),
                tuple(sorted((face[1], face[2]))),
                tuple(sorted((face[2], face[0]))),
            )
        )
        signed_volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        ) / 6

    bad_edges = [edge for edge, count in edge_counts.items() if count != 2]
    if bad_edges:
        raise MeshValidationError(
            f"{mesh.name} is not a closed 2-manifold ({len(bad_edges)} invalid edges)"
        )
    if signed_volume <= 1e-9:
        raise MeshValidationError(f"{mesh.name} is not outward-wound or has zero volume")


def validate_relief_model(model: ReliefModel) -> None:
    names = tuple(body.name for body in model.bodies)
    expected_names = ("terrain-low", "terrain-high", "water", "track")
    dry_names = ("terrain-low", "terrain-high", "track")
    if names not in {expected_names, dry_names}:
        raise MeshValidationError(
            "relief bodies must be low terrain, high terrain, optional water, track"
        )
    colors = tuple(body.color for body in model.bodies)
    if len(set(colors)) != len(colors) or len(colors) not in {3, 4}:
        raise MeshValidationError("relief must contain three or four distinct materials")
    for body in model.bodies:
        validate_mesh(body)
        xs = [vertex[0] for vertex in body.vertices]
        ys = [vertex[1] for vertex in body.vertices]
        zs = [vertex[2] for vertex in body.vertices]
        if min(xs) < -1e-7 or max(xs) > model.width_mm + 1e-7:
            raise MeshValidationError(f"{body.name} exceeds model width")
        if min(ys) < -1e-7 or max(ys) > model.depth_mm + 1e-7:
            raise MeshValidationError(f"{body.name} exceeds model depth")
        if min(zs) < -1e-7 or max(zs) > model.height_mm + 1e-7:
            raise MeshValidationError(f"{body.name} exceeds model height")
    if model.width_mm > 256 or model.depth_mm > 256:
        raise MeshValidationError("model exceeds the 256 x 256 mm build space")
