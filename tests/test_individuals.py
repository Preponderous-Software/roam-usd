"""M5: creatures as sprite cards, stateful structures and the player marker."""

import json
import logging
import struct
import zlib
from pathlib import Path

import pytest
from pxr import Gf, Usd, UsdGeom, UsdShade

from roam_usd.cli import main
from roam_usd.save import read_save_dir
from roam_usd.usd import LEVEL_SPACING, ExportOptions, build_stage, export_world, inventory_summary
from roam_usd.validate import validation_errors

SOURCE = "fixtures/saves/basic"
R00 = "/World/Surface/Room_0_0"


def png_bytes(width=2, height=2, rgba=(200, 40, 40, 255)) -> bytes:
    """A tiny RGBA PNG generated for these tests (no image file is committed)."""

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + bytes(rgba) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def fake_roam(tmp_path) -> Path:
    """A stand-in Roam checkout holding generated sprites for some creatures (not snake)."""
    root = tmp_path / "roam-checkout"
    images = root / "assets" / "images"
    images.mkdir(parents=True)
    for name in ("chicken", "bear", "npc", "rabbit"):
        (images / f"{name}.png").write_bytes(png_bytes())
    return root


@pytest.fixture
def world(basic_save):
    return read_save_dir(basic_save, source=SOURCE)


@pytest.fixture
def stage(world):
    return build_stage(world)


def world_position(stage, path):
    xf = UsdGeom.Xformable(stage.GetPrimAtPath(path))
    m = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    return m.ExtractTranslation()


def fixture_entity(basic_save, room_file, entity_id):
    room = json.loads((basic_save / "rooms" / room_file).read_text())
    for loc in room["grid"]["locations"]:
        for ent in loc["entities"]:
            if ent["id"] == entity_id:
                return ent
    raise KeyError(entity_id)


@pytest.mark.parametrize(
    "path, tile",
    [
        (f"{R00}/Creatures/Chicken_5_5_1", (5, 5, 0, 0)),
        (f"{R00}/Creatures/Bear_6_5_1", (6, 5, 0, 0)),
        (f"{R00}/Creatures/Npc_12_12_1", (12, 12, 0, 0)),
        ("/World/Surface/Room_n1_0/Creatures/Rabbit_16_1_1", (16, 1, -1, 0)),
        ("/World/Cave_1/Room_0_0/Creatures/Snake_14_13_1", (14, 13, 0, 0)),
        (f"{R00}/Structures/Chest_9_9", (9, 9, 0, 0)),
        (f"{R00}/Structures/YoungCrop_10_9", (10, 9, 0, 0)),
        (f"{R00}/Structures/MatureCrop_11_9", (11, 9, 0, 0)),
    ],
)
def test_individual_prims_at_their_tiles(stage, path, tile):
    tx, ty, rx, ry = tile
    assert stage.GetPrimAtPath(path), path
    p = world_position(stage, path)
    assert p[0] == pytest.approx(17 * rx + tx + 0.5)
    assert p[1] == pytest.approx(-(17 * ry + ty + 0.5))
    level_z = -LEVEL_SPACING if "/Cave_1/" in path else 0.0
    # every fixture creature / structure stands on one ground slab (0.1 tall)
    assert level_z + 0.1 <= p[2] <= level_z + 0.2


def test_creature_custom_data_matches_fixture(stage, basic_save):
    prim = stage.GetPrimAtPath(f"{R00}/Creatures/Chicken_5_5_1")
    ent = fixture_entity(basic_save, "room_0_0.json", "fixture-chicken-1")
    for key in ("energy", "tickCreated", "tickLastReproduced", "imagePath"):
        assert prim.GetCustomDataByKey(f"roam:{key}") == ent[key]
    assert prim.GetCustomDataByKey("roam:id") == "fixture-chicken-1"
    assert prim.GetCustomDataByKey("roam:entityClass") == "Chicken"


def test_npc_custom_data(stage):
    prim = stage.GetPrimAtPath(f"{R00}/Creatures/Npc_12_12_1")
    assert prim.GetCustomDataByKey("roam:npcMode") == "npc"
    assert prim.GetCustomDataByKey("roam:npcInventory") == {"Wheat": 1}


def test_chest_contents_summarised(stage):
    prim = stage.GetPrimAtPath(f"{R00}/Structures/Chest_9_9")
    assert prim.GetCustomDataByKey("roam:storedInventory") == {"Apple": 2, "Banana": 1}
    assert prim.GetCustomDataByKey("roam:id") == "fixture-chest-1"
    assert prim.GetTypeName() == "Mesh"  # from the referenced /World/Prototypes/Chest


def test_crop_tick_planted(stage):
    assert (
        stage.GetPrimAtPath(f"{R00}/Structures/YoungCrop_10_9").GetCustomDataByKey(
            "roam:tickPlanted"
        )
        == 900
    )
    assert (
        stage.GetPrimAtPath(f"{R00}/Structures/MatureCrop_11_9").GetCustomDataByKey(
            "roam:tickPlanted"
        )
        == 500
    )


def test_creature_card_is_a_flat_quad_facing_up(stage):
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(f"{R00}/Creatures/Bear_6_5_1"))
    points = mesh.GetPointsAttr().Get()
    assert len(points) == 4 and {p[2] for p in points} == {0.0}
    a, b, c = (Gf.Vec3d(points[i]) for i in (0, 1, 2))
    normal = Gf.Cross(b - a, c - b)
    assert normal[2] > 0
    assert len(UsdGeom.PrimvarsAPI(mesh).GetPrimvar("st").Get()) == 4


def test_cards_untextured_without_checkout(stage):
    prim = stage.GetPrimAtPath(f"{R00}/Creatures/Bear_6_5_1")
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    assert not material
    assert not stage.GetPrimAtPath("/World/Looks")
    assert UsdGeom.Mesh(prim).GetDisplayColorAttr().Get()


def test_prim_paths_contain_no_uuids(stage):
    for prim in stage.Traverse():
        assert "fixture" not in str(prim.GetPath())


def test_ids_can_be_left_out(world):
    stage = build_stage(world, ExportOptions(include_ids=False))
    prim = stage.GetPrimAtPath(f"{R00}/Creatures/Chicken_5_5_1")
    assert prim.GetCustomDataByKey("roam:id") is None
    assert prim.GetCustomDataByKey("roam:energy") == 80


def test_player_marker(stage):
    prim = stage.GetPrimAtPath("/World/Player")
    assert prim
    p = world_position(stage, "/World/Player")
    assert (p[0], p[1]) == pytest.approx((8.5, -8.5))
    assert p[2] == pytest.approx(0.1)  # on top of the WoodFloor slab
    assert prim.GetCustomDataByKey("roam:locationId") == "r0-0-l-8-8"
    assert stage.GetPrimAtPath("/World/Player/Marker").GetTypeName() == "Mesh"


def test_player_honours_room_z(tmp_path, basic_save):
    save = tmp_path / "save"
    (save / "rooms").mkdir(parents=True)
    for f in (basic_save / "rooms").iterdir():
        (save / "rooms" / f.name).write_bytes(f.read_bytes())
    (save / "playerLocation.json").write_text(
        '{"roomX": 0, "roomY": 0, "roomZ": -1, "locationId": "c-l-13-13"}'
    )
    stage = build_stage(read_save_dir(save))
    p = world_position(stage, "/World/Player")
    assert (p[0], p[1]) == pytest.approx((13.5, -13.5))
    assert p[2] == pytest.approx(-LEVEL_SPACING + 0.1 + 1.2)  # CaveFloor + CaveLadder
    surface_only = build_stage(read_save_dir(save), ExportOptions(levels="surface"))
    assert not surface_only.GetPrimAtPath("/World/Player")


def test_player_tile_missing_warns_and_omits(tmp_path, basic_save, caplog):
    save = tmp_path / "save"
    (save / "rooms").mkdir(parents=True)
    for f in (basic_save / "rooms").iterdir():
        (save / "rooms" / f.name).write_bytes(f.read_bytes())
    (save / "playerLocation.json").write_text(
        '{"roomX": 0, "roomY": 0, "roomZ": 0, "locationId": "nowhere"}'
    )
    with caplog.at_level(logging.WARNING):
        stage = build_stage(read_save_dir(save))
    assert not stage.GetPrimAtPath("/World/Player")
    assert "nowhere" in caplog.text


def test_sprites_from_checkout(world, fake_roam, caplog):
    with caplog.at_level(logging.WARNING, logger="roam_usd.usd"):
        stage = build_stage(world, ExportOptions(roam_checkout=fake_roam))
    for path, sprite in (
        (f"{R00}/Creatures/Chicken_5_5_1", "chicken.png"),
        (f"{R00}/Creatures/Bear_6_5_1", "bear.png"),
        ("/World/Surface/Room_n1_0/Creatures/Rabbit_16_1_1", "rabbit.png"),
    ):
        prim = stage.GetPrimAtPath(path)
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        assert material, path
        texture = UsdShade.Shader(material.GetPrim().GetChild("Sprite"))
        file = Path(texture.GetInput("file").Get().path)
        assert file.is_absolute()
        assert file == (fake_roam / "assets" / "images" / sprite).resolve()
        assert fake_roam.resolve() in file.parents
        surface = UsdShade.Shader(material.GetPrim().GetChild("Surface"))
        (source,) = surface.GetInput("opacity").GetConnectedSources()[0]
        assert source.sourceName == "a"
    # snake.png is missing from the checkout: untextured, warned once
    snake = stage.GetPrimAtPath("/World/Cave_1/Room_0_0/Creatures/Snake_14_13_1")
    assert not UsdShade.MaterialBindingAPI(snake).ComputeBoundMaterial()[0]
    assert sum("snake.png" in r.getMessage() for r in caplog.records) == 1


def test_missing_sprite_warned_once_per_path(world, tmp_path, caplog):
    empty = tmp_path / "empty-checkout"
    empty.mkdir()
    # two creatures share one missing image path -> still one warning
    with caplog.at_level(logging.WARNING, logger="roam_usd.usd"):
        build_stage(world, ExportOptions(roam_checkout=empty))
        messages = [r.getMessage() for r in caplog.records]
    assert sum("chicken.png" in m for m in messages) == 1
    assert len([m for m in messages if "sprite" in m]) == 5  # five distinct image paths


def test_textured_stage_validates(tmp_path, world, fake_roam):
    out = export_world(world, tmp_path / "w.usda", options=ExportOptions(roam_checkout=fake_roam))
    assert validation_errors(str(out)) == []


def test_cli_export_with_roam(tmp_path, basic_save, fake_roam):
    out = tmp_path / "w.usda"
    assert main(["export", str(basic_save), "--roam", str(fake_roam), "-o", str(out)]) == 0
    text = out.read_text()
    assert (fake_roam / "assets" / "images" / "bear.png").resolve().as_posix() in text


def test_cli_export_roam_not_a_dir(tmp_path, basic_save, capsys):
    out = tmp_path / "w.usda"
    assert main(["export", str(basic_save), "--roam", str(tmp_path / "nope"), "-o", str(out)]) == 1
    assert "not a directory" in capsys.readouterr().err


@pytest.mark.parametrize(
    "inventory, summary",
    [
        ({"inventorySlots": []}, {}),
        ({}, {}),
        (None, {}),
        (
            {
                "inventorySlots": [
                    {"slotContents": [{"entityClass": "Stone"}, {"entityClass": "Apple"}]},
                    {"slotContents": [{"entityClass": "Stone"}, {"name": "no class"}]},
                ]
            },
            {"Apple": 1, "Stone": 2},
        ),
    ],
)
def test_inventory_summary(inventory, summary):
    assert inventory_summary(inventory) == summary


@pytest.mark.parametrize("escape", ["outside", "absolute", "symlink"])
def test_sprite_paths_must_stay_inside_the_checkout(tmp_path, fake_roam, caplog, escape):
    outside = tmp_path / "outside.png"
    outside.write_bytes(png_bytes())
    if escape == "outside":
        image_path = "../outside.png"
    elif escape == "absolute":
        image_path = str(outside)
    else:
        (fake_roam / "assets" / "images" / "link.png").symlink_to(outside)
        image_path = "assets/images/link.png"
    save = tmp_path / "save"
    (save / "rooms").mkdir(parents=True)
    room = {
        "backgroundColor": "(1, 2, 3)",
        "x": 0,
        "y": 0,
        "grid": {
            "columns": 1,
            "rows": 1,
            "locations": [
                {
                    "id": "a",
                    "x": 0,
                    "y": 0,
                    "entities": [{"entityClass": "Bear", "imagePath": image_path}],
                }
            ],
        },
    }
    (save / "rooms" / "room_0_0.json").write_text(json.dumps(room))
    with caplog.at_level(logging.WARNING, logger="roam_usd.usd"):
        stage = build_stage(read_save_dir(save), ExportOptions(roam_checkout=fake_roam))
    card = stage.GetPrimAtPath("/World/Surface/Room_0_0/Creatures/Bear_0_0_0")
    assert not UsdShade.MaterialBindingAPI(card).ComputeBoundMaterial()[0]
    assert not stage.GetPrimAtPath("/World/Looks")
    assert "outside" in caplog.text


def test_player_location_id_follows_include_ids(world):
    stage_with = build_stage(world)
    with_ids = stage_with.GetPrimAtPath("/World/Player")
    assert with_ids.GetCustomDataByKey("roam:locationId") == "r0-0-l-8-8"
    stage_without = build_stage(world, ExportOptions(include_ids=False))
    without = stage_without.GetPrimAtPath("/World/Player")
    assert without.GetCustomDataByKey("roam:locationId") is None
    assert without.GetCustomDataByKey("roam:tileX") == 8
