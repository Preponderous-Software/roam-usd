import json
import logging
import re
from collections import Counter

import pytest
from pxr import Gf, Usd, UsdGeom

from conftest import check_golden
from roam_usd import catalog
from roam_usd.cli import main
from roam_usd.model import Room, level_name
from roam_usd.save import read_save_dir
from roam_usd.usd import (
    LEVEL_SPACING,
    ExportError,
    ExportOptions,
    build_stage,
    export_world,
    id_stride,
    instance_id,
    output_format,
    prim_coord,
    prototype_name,
    room_prim_name,
    tile_center,
)
from roam_usd.validate import validation_errors

SOURCE = "fixtures/saves/basic"


@pytest.fixture
def world(basic_save):
    return read_save_dir(basic_save, source=SOURCE)


@pytest.fixture
def stage(world):
    return build_stage(world)


def room_path(level, x, y):
    return f"/World/{level}/Room_{prim_coord(x)}_{prim_coord(y)}"


def instancer_counts(stage, path):
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath(f"{path}/Tiles"))
    targets = inst.GetPrototypesRel().GetTargets()
    names = [stage.GetPrimAtPath(t).GetCustomDataByKey("roam:entityClass") for t in targets]
    return Counter(names[i] for i in inst.GetProtoIndicesAttr().Get())


def test_stage_metadata(stage):
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert stage.GetDefaultPrim().GetPath() == "/World"
    data = stage.GetRootLayer().customLayerData
    assert data["roam:source"] == "save"
    assert data["roam:savePath"] == SOURCE
    assert data["roam:tick"] == 1200
    assert "roam-usd:version" in data


def test_one_room_prim_per_room_file_under_its_level(stage, basic_save):
    expected = set()
    for f in (basic_save / "rooms").iterdir():
        x, y, *z = (int(v) for v in re.findall(r"-?\d+", f.stem))
        level = "Surface" if not z or z[0] == 0 else f"Cave_{-z[0]}"
        expected.add(room_path(level, x, y))
    found = {str(p.GetPath()) for p in stage.Traverse() if p.GetName().startswith("Room_")}
    assert found == expected


def test_level_prims_stacked_four_units_apart(stage):
    for name, z in (("Surface", 0), ("Cave_1", -1)):
        xform = UsdGeom.Xformable(stage.GetPrimAtPath(f"/World/{name}"))
        translate = xform.GetLocalTransformation().ExtractTranslation()
        assert translate == Gf.Vec3d(0, 0, z * LEVEL_SPACING)
    assert LEVEL_SPACING == 4


def test_room_type_custom_data(stage):
    assert (
        stage.GetPrimAtPath(room_path("Surface", 0, 0)).GetCustomDataByKey("roam:roomType")
        == "forest"
    )
    assert (
        stage.GetPrimAtPath(room_path("Surface", -1, 0)).GetCustomDataByKey("roam:roomType")
        == "unknown"
    )
    assert (
        stage.GetPrimAtPath(room_path("Cave_1", 0, 0)).GetCustomDataByKey("roam:roomType") == "cave"
    )


def test_ground_colour_from_background(stage):
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(room_path("Surface", 1, -1) + "/Ground"))
    (color,) = mesh.GetDisplayColorAttr().Get()
    assert color == Gf.Vec3f(105 / 255, 104 / 255, 103 / 255)
    extent = mesh.GetExtentAttr().Get()
    assert (extent[0], extent[1]) == (Gf.Vec3f(0, -5, 0), Gf.Vec3f(5, 0, 0))


def test_instance_counts_match_room_files(stage, world):
    for room in world.rooms():
        level = "Surface" if room.z == 0 else f"Cave_{-room.z}"
        expected = Counter(
            e.entity_class
            for t in room.tiles
            for e in t.entities
            if not catalog.lookup(e.entity_class).individual
        )
        assert instancer_counts(stage, room_path(level, room.x, room.y)) == expected


def test_individual_classes_not_instanced(stage):
    counts = instancer_counts(stage, room_path("Surface", 0, 0))
    for cls in ("Chicken", "Bear", "Npc", "Chest", "YoungCrop", "MatureCrop", "Player"):
        assert cls not in counts


def test_instancer_arrays_agree(stage):
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.PointInstancer):
            inst = UsdGeom.PointInstancer(prim)
            n = len(inst.GetProtoIndicesAttr().Get())
            assert len(inst.GetPositionsAttr().Get()) == n
            assert len(inst.GetIdsAttr().Get()) == n
            assert len(set(inst.GetIdsAttr().Get())) == n


def world_positions(stage, path):
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath(f"{path}/Tiles"))
    to_world = UsdGeom.Xformable(inst.GetPrim()).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()
    )
    ids = list(inst.GetIdsAttr().Get())
    return {
        i: to_world.Transform(Gf.Vec3d(p))
        for i, p in zip(ids, inst.GetPositionsAttr().Get(), strict=True)
    }


@pytest.mark.parametrize(
    "level, rx, ry, tx, ty",
    [
        ("Surface", 0, 0, 1, 0),
        ("Surface", 0, 0, 13, 13),
        ("Surface", -1, 0, 16, 1),
        ("Surface", -1, 0, 4, 2),
    ],
)
def test_tile_centres_on_17_grid(stage, level, rx, ry, tx, ty):
    positions = world_positions(stage, room_path(level, rx, ry))
    p = positions[instance_id(tx, ty, 17, 0)]
    assert p[0] == pytest.approx(17 * rx + tx + 0.5)
    assert p[1] == pytest.approx(-(17 * ry + ty + 0.5))
    assert p[2] == pytest.approx(0.0)


def test_cave_tiles_sit_below_the_surface(stage):
    positions = world_positions(stage, room_path("Cave_1", 0, 0))
    p = positions[instance_id(13, 13, 17, 0)]
    assert (p[0], p[1], p[2]) == pytest.approx((13.5, -13.5, -4.0))


def test_small_grid_room_offset(stage):
    # room (1, -1) has a 5x5 grid: offset is (x * columns, -y * rows)
    positions = world_positions(stage, room_path("Surface", 1, -1))
    p = positions[instance_id(4, 4, 5, 0)]
    assert (p[0], p[1]) == pytest.approx((1 * 5 + 4.5, -(-1 * 5 + 4.5)))


def test_stacks_rise_in_file_order(stage):
    positions = world_positions(stage, room_path("Surface", 0, 0))
    grass = positions[instance_id(3, 3, 17, 0)]
    leaves = positions[instance_id(3, 3, 17, 1)]
    apple = positions[instance_id(3, 3, 17, 2)]
    assert grass[2] < leaves[2] < apple[2]
    assert leaves[2] == pytest.approx(catalog.lookup("Grass").stack_height)


def test_ids_derive_from_coordinates_not_uuids(stage):
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath(room_path("Surface", 0, 0) + "/Tiles"))
    assert instance_id(1, 0, 17, 1) in inst.GetIdsAttr().Get()  # Stone on Grass at (1, 0)
    for prim in stage.Traverse():
        assert "fixture" not in str(prim.GetPath())


def test_unknown_class_uses_fallback(stage, caplog):
    proto = stage.GetPrimAtPath(room_path("Surface", -1, 0) + "/Tiles/Prototypes/Glowshroom")
    assert proto.GetCustomDataByKey("roam:entityClass") == "Glowshroom"
    assert proto.GetTypeName() == "Mesh"
    (color,) = UsdGeom.Mesh(proto).GetDisplayColorAttr().Get()
    assert color == Gf.Vec3f(1, 0, 1)


def test_unknown_class_warns_once(world, caplog):
    with caplog.at_level(logging.WARNING, logger="roam_usd.catalog"):
        build_stage(world)
    assert sum("Glowshroom" in r.getMessage() for r in caplog.records) == 1


def test_prototypes_are_abstract(stage):
    protos = stage.GetPrimAtPath("/World/Prototypes")
    assert protos.IsAbstract()
    assert stage.GetPrimAtPath("/World/Prototypes/Grass").GetTypeName() == "Mesh"


def test_surface_only(world):
    stage = build_stage(world, ExportOptions(levels="surface"))
    assert stage.GetPrimAtPath("/World/Surface")
    assert not stage.GetPrimAtPath("/World/Cave_1")


def test_stage_validates_without_errors(tmp_path, world):
    out = export_world(world, tmp_path / "world.usda")
    assert Usd.Stage.Open(str(out))
    assert validation_errors(str(out)) == []


def test_validator_reports_real_errors():
    # Negative control: a stage with no upAxis/metersPerUnit/defaultPrim is reported.
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    assert validation_errors(stage)


def test_export_twice_is_byte_identical(tmp_path, world):
    a = export_world(world, tmp_path / "a.usda")
    b = export_world(world, tmp_path / "b.usda")
    assert a.read_bytes() == b.read_bytes()


def test_export_from_fresh_reads_is_byte_identical(tmp_path, basic_save):
    a = export_world(read_save_dir(basic_save, source=SOURCE), tmp_path / "a.usda")
    b = export_world(read_save_dir(basic_save, source=SOURCE), tmp_path / "b.usda")
    assert a.read_bytes() == b.read_bytes()


def test_binary_has_same_prims(tmp_path, world):
    text = export_world(world, tmp_path / "w.usda")
    binary = export_world(world, tmp_path / "w.usdc", binary=True)
    assert binary.read_bytes()[:8] == b"PXR-USDC"
    a, b = Usd.Stage.Open(str(text)), Usd.Stage.Open(str(binary))
    assert [p.GetPath() for p in a.Traverse()] == [p.GetPath() for p in b.Traverse()]


@pytest.mark.parametrize("name, binary", [("w.usdc", False), ("w.usda", True), ("w.txt", False)])
def test_extension_must_match_format(tmp_path, world, name, binary):
    with pytest.raises(ExportError):
        export_world(world, tmp_path / name, binary=binary)


def test_dot_usd_accepts_either_format(tmp_path, world):
    assert export_world(world, tmp_path / "t.usd").read_bytes().startswith(b"#usda")
    assert export_world(world, tmp_path / "b.usd", binary=True).read_bytes()[:8] == b"PXR-USDC"


def test_golden_fixture_export(tmp_path, world, update_golden):
    out = export_world(world, tmp_path / "basic.usda")
    check_golden(out, "basic.usda", update_golden)


def test_cli_export(tmp_path, basic_save, capsys):
    out = tmp_path / "out.usda"
    assert main(["export", str(basic_save), "-o", str(out)]) == 0
    assert "wrote" in capsys.readouterr().out
    stage = Usd.Stage.Open(str(out))
    assert stage.GetPrimAtPath(room_path("Cave_1", 0, 0))


def test_cli_export_binary_default_name(tmp_path, basic_save, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["export", str(basic_save), "--binary", "--levels", "surface"]) == 0
    stage = Usd.Stage.Open(str(tmp_path / "world.usdc"))
    assert not stage.GetPrimAtPath("/World/Cave_1")


def test_cli_export_errors(tmp_path, basic_save, malformed_save, capsys):
    assert main(["export", str(malformed_save), "-o", str(tmp_path / "m.usda")]) == 1
    assert "missing field 'grid'" in capsys.readouterr().err
    assert main(["export", str(basic_save), "--binary", "-o", str(tmp_path / "m.usda")]) == 1
    assert "--binary" in capsys.readouterr().err


def test_tall_stacks_get_a_wider_id_stride(tmp_path):
    rooms = tmp_path / "rooms"
    rooms.mkdir()
    tall = [{"entityClass": "Grass"} for _ in range(20)]
    room = {
        "backgroundColor": "(1, 2, 3)",
        "x": 0,
        "y": 0,
        "grid": {
            "columns": 2,
            "rows": 1,
            "locations": [
                {"id": "a", "x": 0, "y": 0, "entities": tall},
                {"id": "b", "x": 1, "y": 0, "entities": [{"entityClass": "Stone"}]},
            ],
        },
    }
    (rooms / "room_0_0.json").write_text(json.dumps(room))
    stage = build_stage(read_save_dir(tmp_path))
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath("/World/Surface/Room_0_0/Tiles"))
    ids = list(inst.GetIdsAttr().Get())
    assert len(ids) == len(set(ids)) == 21
    assert inst.GetPrim().GetCustomDataByKey("roam:idStride") == 32
    assert instance_id(1, 0, 2, 0, 32) in ids  # the Stone, not colliding with stack index 16


def test_id_stride():
    assert id_stride(0) == id_stride(15) == 16
    assert id_stride(16) == id_stride(31) == 32
    assert id_stride(100) == 128
    with pytest.raises(ValueError):
        instance_id(0, 0, 17, 16)


@pytest.mark.parametrize(
    "z, name",
    [(0, "Surface"), (-1, "Cave_1"), (-3, "Cave_3"), (2, "Level_2")],
)
def test_level_name(z, name):
    assert level_name(z) == name


@pytest.mark.parametrize(
    "x, y, name",
    [(0, 0, "Room_0_0"), (-1, 2, "Room_n1_2"), (3, -12, "Room_3_n12")],
)
def test_room_prim_name(x, y, name):
    room = Room(x, y, 0, 17, 17, (0, 0, 0), "unknown", "", ())
    assert room_prim_name(room) == name


def test_tile_center_flips_y():
    assert tile_center(0, 0) == (0.5, -0.5)
    assert tile_center(16, 3) == (16.5, -3.5)


@pytest.mark.parametrize(
    "entity_class, name",
    [("Grass", "Grass"), ("Glow-shroom", "Glow_shroom"), ("Odd Thing", "Odd_Thing")],
)
def test_prototype_name_is_a_valid_identifier(entity_class, name):
    assert prototype_name(entity_class) == name


@pytest.mark.parametrize(
    "path, binary, fmt",
    [
        ("w.usda", False, "usda"),
        ("w.usdc", True, "usdc"),
        ("W.USDA", False, "usda"),
        ("W.USDC", True, "usdc"),
        ("w.usd", False, "usda"),
        ("w.usd", True, "usdc"),
    ],
)
def test_output_format(path, binary, fmt):
    assert output_format(path, binary) == fmt


def test_output_format_error_names_the_flag():
    with pytest.raises(ExportError, match="without --binary needs a .usda or .usd name"):
        output_format("w.usdc", False)
    with pytest.raises(ExportError, match="with --binary needs a .usdc or .usd name"):
        output_format("w.usda", True)


def test_unwritable_destination_is_an_export_error(tmp_path, world, basic_save, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(ExportError):
        export_world(world, blocker / "sub" / "w.usda")
    assert main(["export", str(basic_save), "-o", str(blocker / "w.usda")]) == 1
    assert "roam-usd export:" in capsys.readouterr().err
