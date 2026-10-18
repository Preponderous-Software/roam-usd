import json
import logging

import pytest

from roam_usd.cli import main
from roam_usd.model import UNKNOWN_ROOM_TYPE
from roam_usd.save import SaveFormatError, parse_background_color, read_save_dir


def write_room(save_dir, name, room):
    rooms = save_dir / "rooms"
    rooms.mkdir(parents=True, exist_ok=True)
    (rooms / name).write_text(json.dumps(room), encoding="utf-8")


def minimal_room(x=0, y=0, **extra):
    room = {
        "backgroundColor": "(1, 2, 3)",
        "x": x,
        "y": y,
        "grid": {"columns": 3, "rows": 3, "locations": []},
    }
    room.update(extra)
    return room


def test_levels_and_rooms(basic_save):
    world = read_save_dir(basic_save)
    assert [lvl.z for lvl in world.levels] == [0, -1]
    assert [lvl.name for lvl in world.levels] == ["Surface", "Cave_1"]
    surface = {(r.x, r.y) for r in world.levels[0].rooms}
    assert surface == {(0, 0), (-1, 0), (1, -1)}
    assert [(r.x, r.y, r.z) for r in world.levels[1].rooms] == [(0, 0, -1)]


def test_missing_z_reads_as_surface(basic_save):
    world = read_save_dir(basic_save)
    room = world.find_room(-1, 0, 0)
    assert room is not None and room.z == 0


def test_room_type_kept_when_present_else_unknown(basic_save):
    world = read_save_dir(basic_save)
    assert world.find_room(0, 0, 0).room_type == "forest"
    assert world.find_room(0, 0, -1).room_type == "cave"
    assert world.find_room(-1, 0, 0).room_type == UNKNOWN_ROOM_TYPE == "unknown"


def test_room_type_never_inferred_from_contents(tmp_path):
    # A room full of ore with no roomType must stay "unknown", not "mountain".
    room = minimal_room()
    room["grid"]["locations"] = [
        {"id": "a", "x": 0, "y": 0, "entities": [{"entityClass": "IronOre"}]},
        {"id": "b", "x": 1, "y": 0, "entities": [{"entityClass": "GoldOre"}]},
    ]
    write_room(tmp_path, "room_0_0.json", room)
    assert read_save_dir(tmp_path).find_room(0, 0, 0).room_type == "unknown"


def test_negative_coordinates_and_small_grid(basic_save):
    room = read_save_dir(basic_save).find_room(1, -1, 0)
    assert (room.columns, room.rows) == (5, 5)
    assert room.background_color == (105, 104, 103)
    assert {(t.x, t.y) for t in room.tiles} == {(0, 0), (1, 0), (2, 0), (3, 0), (4, 4)}


def test_grid_size_read_from_file(basic_save):
    room = read_save_dir(basic_save).find_room(0, 0, 0)
    assert (room.columns, room.rows) == (17, 17)


def test_player_records_skipped_and_order_preserved(basic_save):
    room = read_save_dir(basic_save).find_room(0, 0, 0)
    tile = room.tile_by_location_id("r0-0-l-8-8")
    assert [e.entity_class for e in tile.entities] == ["WoodFloor"]
    assert "Player" not in read_save_dir(basic_save).entity_counts()
    stacked = room.tile_by_location_id("r0-0-l-3-3")
    assert [e.entity_class for e in stacked.entities] == ["Grass", "Leaves", "Apple"]
    assert [e.stack_index for e in stacked.entities] == [0, 1, 2]


def test_stack_index_counts_skipped_player(tmp_path):
    room = minimal_room()
    room["grid"]["locations"] = [
        {
            "id": "a",
            "x": 0,
            "y": 0,
            "entities": [{"entityClass": "Player"}, {"entityClass": "Grass"}],
        }
    ]
    write_room(tmp_path, "room_0_0.json", room)
    (tile,) = read_save_dir(tmp_path).find_room(0, 0, 0).tiles
    assert [(e.entity_class, e.stack_index) for e in tile.entities] == [("Grass", 1)]


def test_unknown_classes_kept(basic_save):
    counts = read_save_dir(basic_save).entity_counts()
    assert counts["Glowshroom"] == 1


def test_entity_fields_kept(basic_save):
    room = read_save_dir(basic_save).find_room(0, 0, 0)
    (chicken,) = [e for t in room.tiles for e in t.entities if e.entity_class == "Chicken"]
    assert chicken.data["energy"] == 80
    assert chicken.data["imagePath"] == "assets/images/chicken.png"
    assert chicken.id == "fixture-chicken-1"


def test_player_and_tick(basic_save):
    world = read_save_dir(basic_save)
    assert world.tick == 1200
    p = world.player
    assert (p.room_x, p.room_y, p.room_z, p.location_id) == (0, 0, 0, "r0-0-l-8-8")


def test_player_room_z_defaults_to_zero(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / "playerLocation.json").write_text('{"roomX": 0, "roomY": 0, "locationId": "x"}')
    assert read_save_dir(tmp_path).player.room_z == 0


def test_missing_player_and_tick_are_optional(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    world = read_save_dir(tmp_path)
    assert world.player is None and world.tick is None


def test_malformed_room_names_file_and_field(malformed_save):
    with pytest.raises(SaveFormatError) as exc:
        read_save_dir(malformed_save)
    message = str(exc.value)
    assert "room_0_0.json" in message and "'grid'" in message


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda r: r.pop("x"), "'x'"),
        (lambda r: r.update(y="1"), "'y'"),
        (lambda r: r.update(z=True), "'z'"),
        (lambda r: r["grid"].pop("columns"), "'columns'"),
        (lambda r: r["grid"].update(locations=[{"x": 0, "y": 0}]), "'entities'"),
        (
            lambda r: r["grid"].update(locations=[{"x": 0, "y": 0, "entities": [{}]}]),
            "'entityClass'",
        ),
        (lambda r: r["grid"].update(locations=[{"x": 5, "y": 0, "entities": []}]), "outside"),
        (lambda r: r.update(backgroundColor="(1, 2)"), "backgroundColor"),
    ],
)
def test_invalid_room_fields(tmp_path, mutate, field):
    room = minimal_room()
    mutate(room)
    write_room(tmp_path, "room_0_0.json", room)
    with pytest.raises(SaveFormatError) as exc:
        read_save_dir(tmp_path)
    assert "room_0_0.json" in str(exc.value) and field in str(exc.value)


def test_invalid_json_names_file(tmp_path):
    (tmp_path / "rooms").mkdir()
    (tmp_path / "rooms" / "room_2_3.json").write_text("{not json")
    with pytest.raises(SaveFormatError, match="room_2_3.json"):
        read_save_dir(tmp_path)


def test_filename_disagreement_warns_and_uses_contents(tmp_path, caplog):
    write_room(tmp_path, "room_4_4.json", minimal_room(x=1, y=2))
    with caplog.at_level(logging.WARNING):
        world = read_save_dir(tmp_path)
    assert world.find_room(1, 2, 0) is not None
    assert "disagree" in caplog.text


def test_duplicate_room_is_an_error(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    write_room(tmp_path, "room_0_0_0.json", minimal_room(z=0))
    with pytest.raises(SaveFormatError, match="also stored"):
        read_save_dir(tmp_path)


def test_non_room_files_ignored(tmp_path, caplog):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / "rooms" / "notes.txt").write_text("hi")
    with caplog.at_level(logging.WARNING):
        assert len(read_save_dir(tmp_path).rooms()) == 1
    assert "notes.txt" in caplog.text


def test_not_a_save_dir(tmp_path):
    with pytest.raises(SaveFormatError, match="rooms/"):
        read_save_dir(tmp_path)


@pytest.mark.parametrize(
    "text, rgb",
    [("(203, 134, 61)", (203, 134, 61)), ("(0,0,0)", (0, 0, 0)), ([1, 2, 3], (1, 2, 3))],
)
def test_parse_background_color(text, rgb):
    assert parse_background_color(text) == rgb


@pytest.mark.parametrize("bad", ["(1, 2, 3, 4)", "(a, b, c)", "(1, 2, 300)", 7])
def test_parse_background_color_rejects(bad):
    with pytest.raises(SaveFormatError):
        parse_background_color(bad)


def test_info_command(basic_save, capsys):
    assert main(["info", str(basic_save)]) == 0
    out = capsys.readouterr().out
    assert "Rooms: 4" in out
    assert "Surface (z=0): 3 rooms" in out
    assert "Cave_1 (z=-1): 1 room" in out
    assert "Entities: 43" in out
    rows = [line.split() for line in out.splitlines() if line.startswith("  ")]
    lines = {row[0]: row[-1] for row in rows}
    assert lines["Grass"] == "15"
    assert lines["Stone"] == "4"
    assert lines["Glowshroom"] == "1"
    assert "Player" not in lines
    assert "Player: room (0, 0, 0), tile (8, 8)" in out
    assert "Tick: 1200" in out


def test_info_command_malformed(malformed_save, capsys):
    assert main(["info", str(malformed_save)]) == 1
    assert "missing field 'grid'" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [42, "", "   ", ["forest"], {"type": "forest"}])
def test_invalid_room_type_rejected(tmp_path, bad):
    write_room(tmp_path, "room_0_0.json", minimal_room(roomType=bad))
    with pytest.raises(SaveFormatError) as exc:
        read_save_dir(tmp_path)
    assert "room_0_0.json" in str(exc.value) and "'roomType'" in str(exc.value)


def test_null_room_type_reads_as_unknown(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room(roomType=None))
    assert read_save_dir(tmp_path).find_room(0, 0, 0).room_type == "unknown"


@pytest.mark.parametrize("name", ["rooms/room_0_0.json", "playerLocation.json", "tick.json"])
def test_invalid_utf8_names_file(tmp_path, name, capsys):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / name).write_bytes(b'{"tick": "\xff\xfe"}')
    with pytest.raises(SaveFormatError, match="UTF-8") as exc:
        read_save_dir(tmp_path)
    assert name.split("/")[-1] in str(exc.value)
    assert main(["info", str(tmp_path)]) == 1
    assert "UTF-8" in capsys.readouterr().err
