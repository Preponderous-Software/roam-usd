import json
import logging

import pytest

from roam_usd.cli import main
from roam_usd.model import UNKNOWN_ROOM_TYPE
from roam_usd.save import (
    SaveFormatError,
    format_info,
    parse_background_color,
    parse_player_location,
    parse_room,
    parse_tick,
    read_save_dir,
)


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


def test_parse_player_location():
    p = parse_player_location({"roomX": -1, "roomY": 2, "roomZ": -3, "locationId": "l"}, "p")
    assert (p.room_x, p.room_y, p.room_z, p.location_id) == (-1, 2, -3, "l")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"roomY": 0, "locationId": "l"}, "missing field 'roomX'"),
        ({"roomX": True, "roomY": 0, "locationId": "l"}, "invalid field 'roomX'"),
        ({"roomX": 0, "roomY": 0, "roomZ": "1", "locationId": "l"}, "invalid field 'roomZ'"),
        ({"roomX": 0, "roomY": 0, "roomZ": False, "locationId": "l"}, "invalid field 'roomZ'"),
        ({"roomX": 0, "roomY": 0}, "missing field 'locationId'"),
        ({"roomX": 0, "roomY": 0, "locationId": 7}, "invalid field 'locationId'"),
        ([], "missing field 'roomX'"),
    ],
)
def test_parse_player_location_rejects(data, message):
    with pytest.raises(SaveFormatError, match=message) as exc:
        parse_player_location(data, "save/playerLocation.json")
    assert str(exc.value).startswith("save/playerLocation.json: ")


def test_parse_tick():
    assert parse_tick({"tick": 0}, "t") == 0
    assert parse_tick({"tick": 1200}, "t") == 1200


@pytest.mark.parametrize(
    "data, message",
    [
        ({}, "missing field 'tick'"),
        ({"tick": True}, "invalid field 'tick'"),
        ({"tick": "1200"}, "invalid field 'tick'"),
        ({"tick": 12.5}, "invalid field 'tick'"),
        (1200, "missing field 'tick'"),
    ],
)
def test_parse_tick_rejects(data, message):
    with pytest.raises(SaveFormatError, match=message) as exc:
        parse_tick(data, "save/tick.json")
    assert str(exc.value).startswith("save/tick.json: ")


def test_invalid_player_location_names_the_file(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / "playerLocation.json").write_text(
        '{"roomX": 0, "roomY": 0, "roomZ": "deep", "locationId": "x"}'
    )
    with pytest.raises(SaveFormatError, match=r"playerLocation\.json: invalid field 'roomZ'"):
        read_save_dir(tmp_path)


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
        (lambda r: r.update(x=True), "'x'"),
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


@pytest.mark.parametrize("data", [[], "room", 7, None])
def test_room_file_must_hold_an_object(tmp_path, data):
    write_room(tmp_path, "room_0_0.json", data)
    with pytest.raises(SaveFormatError, match=r"room_0_0\.json: a room file must hold"):
        read_save_dir(tmp_path)


@pytest.mark.parametrize("columns, rows", [(0, 3), (3, 0), (-1, 3)])
def test_grid_size_must_be_positive(tmp_path, columns, rows):
    room = minimal_room()
    room["grid"].update(columns=columns, rows=rows)
    write_room(tmp_path, "room_0_0.json", room)
    with pytest.raises(SaveFormatError, match=f"got {columns}x{rows}"):
        read_save_dir(tmp_path)


def test_duplicate_tile_is_an_error(tmp_path):
    room = minimal_room()
    room["grid"]["locations"] = [{"x": 1, "y": 2, "entities": []}] * 2
    write_room(tmp_path, "room_0_0.json", room)
    with pytest.raises(SaveFormatError, match=r"locations\[1\]: duplicate tile \(1, 2\)"):
        read_save_dir(tmp_path)


def test_tiles_sorted_by_row_then_column():
    room = minimal_room()
    room["grid"]["locations"] = [
        {"x": x, "y": y, "entities": []} for x, y in [(0, 1), (2, 0), (1, 0)]
    ]
    tiles = parse_room(room, "room").tiles
    assert [(t.x, t.y) for t in tiles] == [(1, 0), (2, 0), (0, 1)]


@pytest.mark.parametrize(
    "extra, name",
    [({}, "(2, -1)"), ({"name": "Home"}, "Home"), ({"name": 7}, "(2, -1)")],
)
def test_room_name_falls_back_to_its_coordinates(extra, name):
    assert parse_room(minimal_room(x=2, y=-1, **extra), "room").name == name


def test_levels_run_downwards_with_rooms_sorted_by_row_then_column(tmp_path):
    write_room(tmp_path, "room_0_0_-2.json", minimal_room(z=-2))
    write_room(tmp_path, "room_1_1.json", minimal_room(x=1, y=1))
    write_room(tmp_path, "room_1_0.json", minimal_room(x=1, y=0))
    write_room(tmp_path, "room_0_1.json", minimal_room(x=0, y=1))
    write_room(tmp_path, "room_0_0_-1.json", minimal_room(z=-1))
    world = read_save_dir(tmp_path)
    assert [level.z for level in world.levels] == [0, -1, -2]
    assert [(r.x, r.y) for r in world.levels[0].rooms] == [(1, 0), (0, 1), (1, 1)]


def test_a_file_is_not_a_save_dir(tmp_path):
    path = tmp_path / "save.json"
    path.write_text("{}")
    with pytest.raises(SaveFormatError, match="not a directory"):
        read_save_dir(path)


def test_info_without_tick_or_player(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    lines = format_info(read_save_dir(tmp_path, source="label")).splitlines()
    assert lines == [
        "World: label",
        "Tick: unknown",
        "Rooms: 1",
        "  Surface (z=0): 1 room",
        "Entities: 0",
        "Player: not recorded",
    ]


def test_info_shows_a_tick_of_zero(tmp_path):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / "tick.json").write_text('{"tick": 0}')
    assert "Tick: 0" in format_info(read_save_dir(tmp_path)).splitlines()


@pytest.mark.parametrize("room_x, location_id", [(0, "nowhere"), (9, "anything")])
def test_info_player_tile_not_found(tmp_path, room_x, location_id):
    write_room(tmp_path, "room_0_0.json", minimal_room())
    (tmp_path / "playerLocation.json").write_text(
        json.dumps({"roomX": room_x, "roomY": 0, "locationId": location_id})
    )
    lines = format_info(read_save_dir(tmp_path)).splitlines()
    assert f"Player: room ({room_x}, 0, 0), tile not found" in lines
