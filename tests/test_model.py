"""Direct tests for the world model's lookups and counts (model.py).

The save-reader tests reach these helpers only through worlds that contain what is
looked up; these build tiny worlds in code to pin the miss and aggregation paths.
"""

from collections import Counter

from roam_usd.model import EntityRecord, Level, Room, Tile, World


def tile(x: int, y: int, *classes: str) -> Tile:
    return Tile(
        x=x,
        y=y,
        location_id=f"{x}-{y}",
        entities=tuple(EntityRecord(name, i) for i, name in enumerate(classes)),
    )


def room(x: int, y: int, z: int, *tiles: Tile) -> Room:
    return Room(
        x=x,
        y=y,
        z=z,
        columns=2,
        rows=2,
        background_color=(0, 0, 0),
        room_type="forest",
        name=f"room_{x}_{y}_{z}",
        tiles=tiles,
    )


def world() -> World:
    surface = Level(
        0,
        (
            room(0, 0, 0, tile(0, 0, "Grass", "OakWood"), tile(1, 0, "Grass")),
            room(1, 0, 0, tile(0, 0, "Grass", "Chicken")),
        ),
    )
    cave = Level(-1, (room(0, 0, -1, tile(0, 0, "CaveFloor", "Stone")),))
    return World(levels=(surface, cave))


def test_entity_record_id_is_read_from_data_as_a_string():
    assert EntityRecord("Chicken", 0, {"id": "abc"}).id == "abc"
    assert EntityRecord("Chicken", 0, {"id": 42}).id == "42"
    assert EntityRecord("Chicken", 0, {"id": 0}).id == "0"  # falsy but present


def test_entity_record_without_an_id():
    assert EntityRecord("Grass", 0).id is None
    assert EntityRecord("Grass", 0, {"id": None}).id is None


def test_entity_record_equality_ignores_data():
    assert EntityRecord("Bear", 1, {"id": "a"}) == EntityRecord("Bear", 1, {"id": "b"})
    assert EntityRecord("Bear", 1) != EntityRecord("Bear", 2)


def test_tile_by_location_id_hit_and_miss():
    home = world().find_room(0, 0, 0)
    assert home.tile_by_location_id("1-0").x == 1
    assert home.tile_by_location_id("9-9") is None


def test_room_entity_counts_cover_every_tile_and_stack():
    home = world().find_room(0, 0, 0)
    assert home.entity_counts() == Counter({"Grass": 2, "OakWood": 1})


def test_level_name_follows_z():
    assert [level.name for level in world().levels] == ["Surface", "Cave_1"]


def test_rooms_are_listed_level_by_level_in_order():
    assert [(r.x, r.y, r.z) for r in world().rooms()] == [(0, 0, 0), (1, 0, 0), (0, 0, -1)]


def test_find_room_matches_all_three_coordinates():
    w = world()
    assert w.find_room(0, 0, -1).name == "room_0_0_-1"
    assert w.find_room(1, 0, -1) is None  # (1, 0) exists only on the surface
    assert w.find_room(5, 5, 0) is None


def test_world_entity_counts_sum_across_levels():
    assert world().entity_counts() == Counter(
        {"Grass": 3, "OakWood": 1, "Chicken": 1, "CaveFloor": 1, "Stone": 1}
    )


def test_empty_world():
    empty = World(levels=())
    assert empty.rooms() == []
    assert empty.find_room(0, 0, 0) is None
    assert empty.entity_counts() == Counter()
