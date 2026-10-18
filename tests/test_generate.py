"""M6: the seed driver and `roam-usd generate`.

Unit tests drive `_driver.py` against a tiny stand-in "checkout" written by these
tests (a test double with Roam's module layout, not Roam code). The `roam`-marked
tests run Roam's real generator from ROAM_USD_ROAM_CHECKOUT.
"""

import json
import textwrap

import pytest
from pxr import Usd

from roam_usd import _driver, catalog
from roam_usd.cli import main
from roam_usd.generate import (
    GenerateError,
    apply_manifest,
    check_checkout,
    driver_result,
    generate_seed,
    layer_data,
    list_roam_classes,
    read_generated,
    read_manifest,
    run_driver,
)
from roam_usd.save import SaveFormatError, read_save_dir
from roam_usd.usd import ExportOptions, export_world
from roam_usd.validate import validation_errors

# A stand-in checkout: just enough of Roam's import layout and method names for the
# driver's plumbing. Rooms are 3x3, every room gets Grass, room (1, 0) and the cave
# below it get a CaveEntrance, and the background colour is drawn from `random`.
FAKE_ROAM = {
    "version.txt": "9.9.9-fake\n",
    "src/rendering/__init__.py": "",
    "src/rendering/nullRenderer.py": "class NullRenderer:\n    pass\n",
    "src/world/__init__.py": "",
    "src/world/map.py": """
        import random


        class Ent:
            def __init__(self, name):
                self.__class__ = type(name, (Ent,), {})


        class Loc:
            def __init__(self, x, y):
                self.x, self.y, self.entities = x, y, {}

            def getEntities(self):
                return self.entities


        class Grid:
            def __init__(self):
                self.locs = {f"{x}-{y}": Loc(x, y) for y in range(3) for x in range(3)}

            def getLocations(self):
                return self.locs

            def getLocation(self, key):
                return self.locs[key]

            def getColumns(self):
                return 3


        class Room:
            def __init__(self, x, y, z, room_type):
                self.x, self.y, self.z, self.room_type = x, y, z, room_type
                self.color = (random.randrange(0, 256), 0, 0)
                self.grid = Grid()
                for i, loc in enumerate(self.grid.locs.values()):
                    loc.entities[i] = Ent("Grass")
                if (x, y) == (1, 0) and z > -2:
                    self.grid.locs["2-2"].entities["e"] = Ent("CaveEntrance")

            def getGrid(self):
                return self.grid

            def getRoomType(self):
                return self.room_type


        class Map:
            SEED_PARAM = False

            def __init__(self, gridSize, renderer, tickCounter, config, **kwargs):
                assert gridSize == 17
                self.kwargs = kwargs

            def generateNewRoom(self, x, y, z=0):
                return Room(x, y, z, "cave" if z < 0 else random.choice(["forest", "jungle"]))
        """,
    "src/world/roomJsonReaderWriter.py": """
        class RoomJsonReaderWriter:
            def __init__(self, gridSize, renderer, tickCounter, config):
                self.entityConstructors = {"Grass": None, "CaveEntrance": None}

            def generateJsonForRoom(self, room):
                locations = []
                for key, loc in room.grid.locs.items():
                    entities = [
                        {"id": str(id(e)), "entityClass": type(e).__name__}
                        for e in loc.getEntities().values()
                    ]
                    locations.append({"id": key, "x": loc.x, "y": loc.y, "entities": entities})
                return {
                    "backgroundColor": str(room.color),
                    "x": room.x,
                    "y": room.y,
                    "z": room.z,
                    "name": "fake",
                    "grid": {"columns": 3, "rows": 3, "locations": locations},
                }
        """,
    "src/world/startingHomeGenerator.py": """
        from world.map import Ent


        class StartingHomeGenerator:
            def generate(self, room):
                room.grid.locs["1-1"].entities["home"] = Ent("WoodFloor")
        """,
    "src/entity/__init__.py": "",
    "src/entity/living/__init__.py": "",
    "src/entity/living/livingEntityRegistry.py": "LIVING_ENTITY_TYPES = {'Bear': None}\n",
}


def write_fake_roam(root, seeded_map=False):
    for rel, text in FAKE_ROAM.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = textwrap.dedent(text)
        if seeded_map and rel == "src/world/map.py":
            text = text.replace("**kwargs):", "seed=None, **kwargs):\n        self.seed = seed")
        path.write_text(text)
    return root


@pytest.fixture
def fake_roam(tmp_path):
    return write_fake_roam(tmp_path / "fake-roam")


# --- spiral order ----------------------------------------------------------------


def test_spiral_order_radius_0_and_1():
    assert _driver.spiral_order(0) == [(0, 0)]
    assert _driver.spiral_order(1) == [
        (0, 0),
        (1, 0),
        (1, 1),
        (0, 1),
        (-1, 1),
        (-1, 0),
        (-1, -1),
        (0, -1),
        (1, -1),
    ]


@pytest.mark.parametrize("radius", [2, 3, 5])
def test_spiral_order_covers_the_square_once(radius):
    order = _driver.spiral_order(radius)
    square = {(x, y) for x in range(-radius, radius + 1) for y in range(-radius, radius + 1)}
    assert len(order) == len(square) and set(order) == square
    assert order[0] == (0, 0)
    # rings are completed in order: ring r never starts before ring r-1 is done
    rings = [max(abs(x), abs(y)) for x, y in order]
    assert rings == sorted(rings)


def test_spiral_order_rejects_negative_radius():
    with pytest.raises(ValueError):
        _driver.spiral_order(-1)


def test_room_file_name():
    assert _driver.room_file_name(-1, 2, 0) == "room_-1_2.json"
    assert _driver.room_file_name(0, 0, -2) == "room_0_0_-2.json"


# --- driver against the stand-in checkout --------------------------------------------


def test_driver_writes_save_and_manifest(tmp_path, fake_roam):
    save = run_driver(fake_roam, 5, tmp_path / "save", radius=1, cave_depth=3)
    manifest = read_manifest(save)
    assert manifest["seed"] == 5 and manifest["radius"] == 1 and manifest["caveDepth"] == 3
    assert manifest["seedMechanism"] == "global-random"
    assert manifest["roamVersion"] == "9.9.9-fake"
    assert manifest["roamCommit"] == "unknown"  # not a git checkout
    assert manifest["home"] is True and manifest["gridSize"] == 17
    order = [tuple(c) for c in manifest["generationOrder"]]
    assert order[:9] == [(x, y, 0) for x, y in _driver.spiral_order(1)]
    # caves only under the room with a CaveEntrance, level by level, to the depth limit
    assert order[9:] == [(1, 0, -1), (1, 0, -2)]
    assert manifest["roomTypes"]["1,0,-1"] == "cave"
    names = sorted(p.name for p in (save / "rooms").iterdir())
    assert "room_1_0_-2.json" in names and "room_0_0.json" in names and len(names) == 11
    world = read_generated(save)
    assert world.find_room(1, 0, -1).room_type == "cave"
    home = world.find_room(0, 0, 0).tile_by_location_id("1-1")
    assert [e.entity_class for e in home.entities] == ["Grass", "WoodFloor"]


def test_driver_no_home_and_depth_limit(tmp_path, fake_roam):
    save = run_driver(fake_roam, 5, tmp_path / "s", radius=1, cave_depth=1, home=False)
    manifest = read_manifest(save)
    assert manifest["home"] is False
    assert [c for c in manifest["generationOrder"] if c[2] < 0] == [[1, 0, -1]]
    home = read_generated(save).find_room(0, 0, 0).tile_by_location_id("1-1")
    assert [e.entity_class for e in home.entities] == ["Grass"]


def test_driver_is_deterministic_per_seed(tmp_path, fake_roam):
    def colours(seed, name):
        world = read_save_dir(run_driver(fake_roam, seed, tmp_path / name, radius=1))
        return [r.background_color for r in world.rooms()]

    assert colours(1, "a") == colours(1, "b")
    assert colours(1, "a") != colours(2, "c")


def test_driver_uses_roam_seed_when_available(tmp_path):
    roam = write_fake_roam(tmp_path / "seeded", seeded_map=True)
    manifest = read_manifest(run_driver(roam, 3, tmp_path / "s", radius=0))
    assert manifest["seedMechanism"] == "roam-seed"


def test_driver_never_writes_into_the_checkout(tmp_path, fake_roam):
    before = sorted(str(p) for p in fake_roam.rglob("*"))
    run_driver(fake_roam, 1, tmp_path / "s", radius=1)
    assert sorted(str(p) for p in fake_roam.rglob("*")) == before


def test_list_classes(fake_roam):
    assert list_roam_classes(fake_roam) == ["Bear", "CaveEntrance", "Grass"]


def test_driver_failure_reports_stderr_and_hint(tmp_path, fake_roam):
    (fake_roam / "src/world/startingHomeGenerator.py").write_text("import structlog_missing\n")
    with pytest.raises(GenerateError) as exc:
        run_driver(fake_roam, 1, tmp_path / "s")
    assert "structlog_missing" in str(exc.value) and "--roam-python" in str(exc.value)


@pytest.mark.parametrize(
    "kwargs, message",
    [({"radius": -1}, "--radius"), ({"cave_depth": 4}, "--cave-depth")],
)
def test_driver_argument_checks(tmp_path, fake_roam, kwargs, message):
    with pytest.raises(GenerateError, match=message):
        run_driver(fake_roam, 1, tmp_path / "s", **kwargs)


def test_not_a_checkout(tmp_path):
    with pytest.raises(GenerateError, match="not a Roam checkout"):
        check_checkout(tmp_path)


# --- manifest -> world / stage ---------------------------------------------------------


def test_manifest_fills_only_missing_room_types(basic_save):
    world = read_save_dir(basic_save)
    manifest = {"roomTypes": {"0,0,0": "jungle", "-1,0,0": "grassland"}}
    updated = apply_manifest(world, manifest)
    assert updated.find_room(0, 0, 0).room_type == "forest"  # the room file wins
    assert updated.find_room(-1, 0, 0).room_type == "grassland"  # filled from the manifest
    assert updated.manifest == manifest


def test_read_manifest_errors(tmp_path):
    with pytest.raises(SaveFormatError):
        read_manifest(tmp_path)
    (tmp_path / "roam-usd-manifest.json").write_text('{"format": "other"}')
    with pytest.raises(SaveFormatError, match="not a roam-usd manifest"):
        read_manifest(tmp_path)


def test_layer_data_is_run_independent():
    data = layer_data(
        {"seed": 4, "roamCommit": "abc", "roamVersion": "1", "radius": 2, "pythonVersion": "x"}
    )
    assert data["roam:source"] == "seed" and data["roam:seed"] == 4
    assert data["roam:commit"] == "abc" and data["roam:version"] == "1"
    assert not any("python" in k.lower() or "path" in k.lower() for k in data)


def test_generate_seed_with_stand_in(tmp_path, fake_roam):
    out = generate_seed(fake_roam, 7, tmp_path / "out", radius=1, keep_save=True)
    assert out == tmp_path / "out" / "seed_7.usda"
    stage = Usd.Stage.Open(str(out))
    data = stage.GetRootLayer().customLayerData
    assert data["roam:seed"] == 7 and data["roam:source"] == "seed"
    assert "roam:savePath" not in data
    room = stage.GetPrimAtPath("/World/Cave_1/Room_1_0")
    assert room.GetCustomDataByKey("roam:roomType") == "cave"
    assert (tmp_path / "out" / "seed_7_save" / "roam-usd-manifest.json").is_file()
    # a second run replaces the kept save instead of mixing old rooms in
    generate_seed(fake_roam, 7, tmp_path / "out", radius=0, keep_save=True)
    assert len(list((tmp_path / "out" / "seed_7_save" / "rooms").iterdir())) == 1


def test_generate_refuses_to_clobber_foreign_dir(tmp_path, fake_roam):
    (tmp_path / "out" / "seed_7_save").mkdir(parents=True)
    with pytest.raises(GenerateError, match="not written by roam-usd"):
        generate_seed(fake_roam, 7, tmp_path / "out", radius=0, keep_save=True)


def test_cli_generate(tmp_path, fake_roam, capsys):
    out = tmp_path / "out"
    args = ["generate", "--roam", str(fake_roam), "--seed", "3", "--radius", "0", "-o", str(out)]
    assert main([*args, "--binary"]) == 0
    assert (out / "seed_3.usdc").read_bytes()[:8] == b"PXR-USDC"
    assert main(["generate", "--roam", str(tmp_path), "--seed", "1", "-o", str(out)]) == 1
    assert "not a Roam checkout" in capsys.readouterr().err


# --- real Roam (opt-in: pytest -m roam) ---------------------------------------------------


@pytest.mark.roam
def test_roam_seed_determinism(tmp_path, roam_checkout, roam_python):
    def run(seed, name):
        return generate_seed(
            roam_checkout, seed, tmp_path / name, radius=1, roam_python=roam_python
        ).read_bytes()

    first, second, other = run(1, "a"), run(1, "b"), run(2, "c")
    assert first == second, "seed 1 twice must give byte-identical stages"
    assert first != other, "seed 2 must give a different stage"


@pytest.mark.roam
def test_roam_generated_save_exports_cleanly(tmp_path, roam_checkout, roam_python):
    save = run_driver(roam_checkout, 1, tmp_path / "save", radius=1, roam_python=roam_python)
    assert main(["export", str(save), "-o", str(tmp_path / "w.usda")]) == 0
    stage_path = tmp_path / "w.usda"
    assert validation_errors(str(stage_path)) == []

    world = read_generated(save)
    manifest = world.manifest
    assert manifest["roamCommit"] != "unknown"
    stage = Usd.Stage.Open(
        str(
            export_world(
                world,
                tmp_path / "seeded.usda",
                options=ExportOptions(layer_data=layer_data(manifest), include_ids=False),
            )
        )
    )
    data = stage.GetRootLayer().customLayerData
    assert data["roam:seed"] == 1 and data["roam:commit"] == manifest["roamCommit"]
    for room in world.rooms():
        level = "Surface" if room.z == 0 else f"Cave_{-room.z}"
        x = f"n{-room.x}" if room.x < 0 else room.x
        y = f"n{-room.y}" if room.y < 0 else room.y
        prim = stage.GetPrimAtPath(f"/World/{level}/Room_{x}_{y}")
        expected = manifest["roomTypes"][f"{room.x},{room.y},{room.z}"]
        assert prim.GetCustomDataByKey("roam:roomType") == expected != "unknown"


@pytest.mark.roam
def test_roam_classes_are_all_in_the_catalog(tmp_path, roam_checkout, roam_python):
    names = set(list_roam_classes(roam_checkout, roam_python))
    save = run_driver(roam_checkout, 1, tmp_path / "save", radius=2, roam_python=roam_python)
    names |= set(read_save_dir(save).entity_counts())
    missing = catalog.missing_classes(names)
    print("Roam entity classes missing from the catalog:", missing or "none")
    assert missing == []


@pytest.mark.roam
def test_roam_checkout_left_untouched(tmp_path, roam_checkout, roam_python):
    import subprocess

    def status():
        return subprocess.run(
            ["git", "-C", str(roam_checkout), "status", "--porcelain", "--ignored"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    before = status()
    generate_seed(roam_checkout, 1, tmp_path / "o", radius=0, roam_python=roam_python)
    assert status() == before


def test_manifest_json_roundtrip(tmp_path, fake_roam):
    save = run_driver(fake_roam, 2, tmp_path / "s", radius=0)
    text = (save / "roam-usd-manifest.json").read_text()
    assert json.loads(text)["format"] == "roam-usd-manifest"


def test_driver_result_ignores_other_stdout():
    noisy = "pygame 2.6.1 (SDL 2.28.4)\nHello from the pygame community.\n"
    assert driver_result(noisy + _driver.RESULT_PREFIX + '["Bear"]\n') == ["Bear"]
    with pytest.raises(GenerateError, match="no result"):
        driver_result(noisy)


def test_noisy_roam_imports_do_not_break_the_driver(tmp_path, fake_roam):
    # A dependency that prints to stdout at import time, as pygame does.
    nr = fake_roam / "src/rendering/nullRenderer.py"
    nr.write_text("print('banner from a dependency')\n" + nr.read_text())
    assert list_roam_classes(fake_roam) == ["Bear", "CaveEntrance", "Grass"]
    save = run_driver(fake_roam, 1, tmp_path / "s", radius=0)
    assert read_manifest(save)["seed"] == 1
