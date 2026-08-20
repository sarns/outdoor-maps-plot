import xml.etree.ElementTree as ET
import zipfile

import lib3mf
import pytest
from pydantic import ValidationError

from outdoor_maps_plot.export_3mf import CORE_NS, MATERIAL_NS, write_3mf
from outdoor_maps_plot.mesh_validation import validate_mesh, validate_relief_model
from outdoor_maps_plot.relief import _build_water, _repair_diagonal_contacts, build_relief_model
from outdoor_maps_plot.relief_options import ReliefConfig
from outdoor_maps_plot.water import WaterArea, WaterFeatures, WaterLine


def _config(**changes: object) -> ReliefConfig:
    return ReliefConfig(width_mm=100, depth_mm=80, mesh_pitch_mm=5, **changes)


def _model(config: ReliefConfig | None = None):
    return build_relief_model(
        [[0, 40, 80], [20, 60, 100], [40, 80, 120]],
        [[(10, 10), (50, 45), (90, 70)]],
        config or _config(),
        WaterFeatures(
            areas=(WaterArea(((5, 5), (40, 5), (40, 30), (5, 30), (5, 5))),),
            lines=(WaterLine(((40, 30), (80, 55)), 2.0),),
        ),
    )


def test_relief_config_defaults_and_limits() -> None:
    config = ReliefConfig()
    assert (config.width_mm, config.depth_mm) == (240, 240)
    assert config.colors == ("#4D6B50", "#8B5A2B", "#2F75B5", "#E4431B")
    assert config.output_format == "3mf"
    with pytest.raises(ValidationError):
        ReliefConfig(width_mm=256.01)
    with pytest.raises(ValidationError, match="six-digit hexadecimal"):
        ReliefConfig(track_color="red")
    with pytest.raises(ValidationError, match="must be distinct"):
        ReliefConfig(track_color="#4D6B50")


def test_builds_exactly_four_watertight_printable_bodies() -> None:
    model = _model()
    validate_relief_model(model)
    assert [body.name for body in model.bodies] == [
        "terrain-low",
        "terrain-high",
        "water",
        "track",
    ]
    assert len({body.color for body in model.bodies}) == 4
    assert model.width_mm == 100
    assert model.depth_mm == 80
    assert all(body.vertices and body.triangles for body in model.bodies)


def test_water_corner_contacts_are_bridged_into_a_manifold_mesh() -> None:
    mask = _repair_diagonal_contacts([[True, False], [False, True]])
    water = _build_water(
        [[2.4, 2.4, 2.4], [2.4, 2.4, 2.4], [2.4, 2.4, 2.4]],
        mask,
        20,
        20,
        0.4,
        "#2F75B5",
    )

    assert water is not None
    validate_mesh(water)


def test_constant_elevation_without_mapped_water_produces_dry_three_part_model() -> None:
    model = build_relief_model(
        [[42, 42], [42, 42]],
        [[(10, 10), (90, 70)]],
        _config(),
    )
    validate_relief_model(model)
    assert model.source_elevation_range == (42, 42)
    assert [body.name for body in model.bodies] == ["terrain-low", "terrain-high", "track"]


def test_normalized_raster_water_mask_builds_blue_water_body() -> None:
    raster = tuple(
        tuple(1 <= row <= 6 and 1 <= column <= 6 for column in range(8)) for row in range(8)
    )
    model = build_relief_model(
        [[row + column for column in range(5)] for row in range(5)],
        [[(10, 10), (90, 70)]],
        _config(),
        WaterFeatures(raster_mask=raster),
    )

    validate_relief_model(model)
    water = next(body for body in model.bodies if body.name == "water")
    assert water.color == "#2F75B5"


def test_terrain_split_through_quantized_grid_vertices_remains_manifold() -> None:
    elevations = [
        [50, 0, 50, 100, 0],
        [0, 100, 0, 50, 100],
        [0, 100, 0, 0, 0],
        [50, 50, 0, 0, 0],
        [100, 50, 0, 100, 0],
    ]

    model = build_relief_model(
        elevations,
        [[(10, 10), (90, 70)]],
        _config(),
    )

    validate_relief_model(model)
    assert model.band_heights_mm[0] == pytest.approx(11.4, abs=0.00001)


def test_rejects_bad_grid_and_route_outside_printable_area() -> None:
    with pytest.raises(ValueError, match="rectangular"):
        build_relief_model([[1, 2], [3]], [[(10, 10), (90, 70)]], _config())
    with pytest.raises(ValueError, match="track width"):
        build_relief_model([[1, 2], [3, 4]], [[(0, 10), (90, 70)]], _config())


def test_writes_four_material_3mf_in_millimetres(tmp_path) -> None:
    model = _model()
    destination = write_3mf(
        model,
        tmp_path / "relief.3mf",
        _config(),
        elevation_attribution="Mapzen Terrain Tiles test attribution",
    )
    with zipfile.ZipFile(destination) as archive:
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}.issubset(
            archive.namelist()
        )
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    namespace = {"m": CORE_NS}
    assert root.attrib["unit"] == "millimeter"
    metadata = {item.attrib["name"]: item.text for item in root.findall("m:metadata", namespace)}
    elevation_key = next(name for name in metadata if name.endswith(":ElevationData"))
    assert "Mapzen Terrain Tiles" in metadata[elevation_key]
    material_namespace = {"m": MATERIAL_NS}
    colors = root.findall(".//m:colorgroup/m:color", material_namespace)
    assert [color.attrib["color"] for color in colors] == [
        "#4D6B50FF",
        "#8B5A2BFF",
        "#2F75B5FF",
        "#E4431BFF",
    ]
    objects = root.findall(".//m:object", namespace)
    assert [item.attrib["name"] for item in objects] == [
        "terrain-low",
        "terrain-high",
        "water",
        "track",
    ]
    assert [item.attrib["pid"] for item in objects] == ["1", "1", "1", "1"]
    assert [item.attrib["pindex"] for item in objects] == ["0", "1", "2", "3"]
    assert not root.findall(".//m:basematerials", namespace)
    assert len(root.findall(".//m:build/m:item", namespace)) == 4

    wrapper = lib3mf.get_wrapper()
    parsed = wrapper.CreateModel()
    parsed.QueryReader("3mf").ReadFromFile(str(destination))
    assert parsed.GetUnit() == lib3mf.ModelUnit.MilliMeter
