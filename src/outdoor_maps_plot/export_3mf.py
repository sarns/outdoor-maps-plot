"""Minimal standards-based 3MF writer for four-material relief models."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from outdoor_maps_plot.mesh_validation import validate_relief_model
from outdoor_maps_plot.relief import ReliefModel
from outdoor_maps_plot.relief_options import ReliefConfig

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def write_3mf(
    model: ReliefModel,
    destination: str | Path,
    config: ReliefConfig | None = None,
    *,
    elevation_attribution: str | None = None,
) -> Path:
    """Validate and write a multi-object 3MF in millimetres."""

    validate_relief_model(model)
    if config is not None and tuple(body.color for body in model.bodies) != config.colors:
        raise ValueError("model material colors do not match ReliefConfig")
    destination = Path(destination)
    if destination.suffix.lower() != ".3mf":
        raise ValueError("3MF destination must use the .3mf extension")
    destination.parent.mkdir(parents=True, exist_ok=True)

    parts = {
        "[Content_Types].xml": _content_types_xml(),
        "_rels/.rels": _relationships_xml(),
        "3D/3dmodel.model": _model_xml(model, elevation_attribution),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return destination


def _model_xml(model: ReliefModel, elevation_attribution: str | None) -> bytes:
    ET.register_namespace("", CORE_NS)
    root = ET.Element(f"{{{CORE_NS}}}model", {"unit": "millimeter", "xml:lang": "en-US"})
    metadata = ET.SubElement(root, f"{{{CORE_NS}}}metadata", {"name": "Title"})
    metadata.text = "Outdoor Maps Plot relief"
    metadata = ET.SubElement(root, f"{{{CORE_NS}}}metadata", {"name": "Application"})
    metadata.text = "outdoor-maps-plot"
    if elevation_attribution:
        metadata = ET.SubElement(root, f"{{{CORE_NS}}}metadata", {"name": "ElevationData"})
        metadata.text = elevation_attribution
    metadata = ET.SubElement(root, f"{{{CORE_NS}}}metadata", {"name": "ElevationRange"})
    metadata.text = (
        f"{model.source_elevation_range[0]:.3f}..{model.source_elevation_range[1]:.3f} m"
    )
    resources = ET.SubElement(root, f"{{{CORE_NS}}}resources")
    materials = ET.SubElement(resources, f"{{{CORE_NS}}}basematerials", {"id": "1"})
    for body in model.bodies:
        ET.SubElement(
            materials,
            f"{{{CORE_NS}}}base",
            {"name": body.name, "displaycolor": f"{body.color}FF"},
        )
    for material_index, body in enumerate(model.bodies):
        object_element = ET.SubElement(
            resources,
            f"{{{CORE_NS}}}object",
            {
                "id": str(material_index + 2),
                "name": body.name,
                "type": "model",
                "pid": "1",
                "pindex": str(material_index),
            },
        )
        mesh = ET.SubElement(object_element, f"{{{CORE_NS}}}mesh")
        vertices = ET.SubElement(mesh, f"{{{CORE_NS}}}vertices")
        for x, y, z in body.vertices:
            ET.SubElement(
                vertices,
                f"{{{CORE_NS}}}vertex",
                {"x": _number(x), "y": _number(y), "z": _number(z)},
            )
        triangles = ET.SubElement(mesh, f"{{{CORE_NS}}}triangles")
        for v1, v2, v3 in body.triangles:
            ET.SubElement(
                triangles,
                f"{{{CORE_NS}}}triangle",
                {"v1": str(v1), "v2": str(v2), "v3": str(v3)},
            )
    build = ET.SubElement(root, f"{{{CORE_NS}}}build")
    for object_id in range(2, 6):
        ET.SubElement(build, f"{{{CORE_NS}}}item", {"objectid": str(object_id)})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _content_types_xml() -> bytes:
    root = ET.Element(f"{{{CONTENT_NS}}}Types")
    ET.SubElement(
        root,
        f"{{{CONTENT_NS}}}Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(
        root,
        f"{{{CONTENT_NS}}}Override",
        {
            "PartName": "/3D/3dmodel.model",
            "ContentType": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
        },
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _relationships_xml() -> bytes:
    root = ET.Element(f"{{{REL_NS}}}Relationships")
    ET.SubElement(
        root,
        f"{{{REL_NS}}}Relationship",
        {
            "Target": "/3D/3dmodel.model",
            "Id": "rel0",
            "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
        },
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _number(value: float) -> str:
    return format(value, ".9g")
