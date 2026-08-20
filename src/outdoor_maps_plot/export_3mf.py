"""Standards-compliant 3MF export for four-material relief models."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import lib3mf

from outdoor_maps_plot.mesh_validation import validate_relief_model
from outdoor_maps_plot.relief import Mesh, ReliefModel
from outdoor_maps_plot.relief_options import ReliefConfig

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MATERIAL_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
APP_METADATA_NS = "https://github.com/sarns/outdoor-maps-plot/3mf/metadata/1"


def write_3mf(
    model: ReliefModel,
    destination: str | Path,
    config: ReliefConfig | None = None,
    *,
    elevation_attribution: str | None = None,
) -> Path:
    """Validate and atomically write a four-object 3MF in millimetres.

    The package is produced and read back with the 3MF Consortium's reference
    implementation. This avoids slicer-specific failures caused by subtly
    invalid hand-written OPC or 3MF XML.
    """

    validate_relief_model(model)
    if config is not None:
        expected = {
            "terrain-low": config.low_color,
            "terrain-high": config.high_color,
            "water": config.water_color,
            "track": config.track_color,
        }
        if any(body.color != expected[body.name] for body in model.bodies):
            raise ValueError("model material colors do not match ReliefConfig")

    destination = Path(destination)
    if destination.suffix.lower() != ".3mf":
        raise ValueError("3MF destination must use the .3mf extension")
    destination.parent.mkdir(parents=True, exist_ok=True)

    wrapper = lib3mf.get_wrapper()
    document = wrapper.CreateModel()
    document.SetUnit(lib3mf.ModelUnit.MilliMeter)
    _add_metadata(document, model, elevation_attribution)

    colors = document.AddColorGroup()
    color_ids = [colors.AddColor(_color(body.color)) for body in model.bodies]
    color_resource_id = colors.GetResourceID()
    identity = wrapper.GetIdentityTransform()

    for body, color_id in zip(model.bodies, color_ids, strict=True):
        mesh = document.AddMeshObject()
        mesh.SetName(body.name)
        mesh.SetGeometry(_positions(body), _triangles(body))
        mesh.SetObjectLevelProperty(color_resource_id, color_id)
        document.AddBuildItem(mesh, identity)

    temporary = _temporary_destination(destination)
    try:
        document.QueryWriter("3mf").WriteToFile(os.fspath(temporary))
        _verify_package(wrapper, temporary)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _add_metadata(document, model: ReliefModel, elevation_attribution: str | None) -> None:
    metadata = document.GetMetaDataGroup()
    metadata.AddMetaData("", "Title", "Outdoor Maps Plot relief", "xs:string", True)
    metadata.AddMetaData("", "Application", "outdoor-maps-plot", "xs:string", True)
    if elevation_attribution:
        metadata.AddMetaData(
            APP_METADATA_NS,
            "ElevationData",
            elevation_attribution,
            "xs:string",
            True,
        )
    metadata.AddMetaData(
        APP_METADATA_NS,
        "ElevationRange",
        f"{model.source_elevation_range[0]:.3f}..{model.source_elevation_range[1]:.3f} m",
        "xs:string",
        True,
    )


def _positions(body: Mesh) -> list:
    positions = []
    for x, y, z in body.vertices:
        position = lib3mf.Position()
        position.Coordinates[0] = float(x)
        position.Coordinates[1] = float(y)
        position.Coordinates[2] = float(z)
        positions.append(position)
    return positions


def _triangles(body: Mesh) -> list:
    triangles = []
    for v1, v2, v3 in body.triangles:
        triangle = lib3mf.Triangle()
        triangle.Indices[0] = v1
        triangle.Indices[1] = v2
        triangle.Indices[2] = v3
        triangles.append(triangle)
    return triangles


def _color(hex_color: str):
    color = lib3mf.Color()
    color.Red = int(hex_color[1:3], 16)
    color.Green = int(hex_color[3:5], 16)
    color.Blue = int(hex_color[5:7], 16)
    color.Alpha = 255
    return color


def _temporary_destination(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}-",
        suffix=".3mf",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(name)
    temporary.unlink()
    return temporary


def _verify_package(wrapper, path: Path) -> None:
    verified = wrapper.CreateModel()
    verified.QueryReader("3mf").ReadFromFile(os.fspath(path))
