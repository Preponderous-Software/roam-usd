"""Seed driver: run Roam's own world generator from a user-supplied Roam checkout.

This file is roam-usd's own (MIT) code. It is executed as a script in a separate
process, with the Roam checkout as the working directory, by ``roam_usd.generate``
(ARCHITECTURE.md § Seed driver). It imports Roam's modules only at run time, from
``<checkout>/src``, and depends on nothing from roam-usd, so any interpreter that
has Roam's requirements installed can run it (``--roam-python``).

It never reads or writes the user's Roam config or saves: ``Map`` gets minimal
stand-ins for ``Config`` and ``TickCounter``, and rooms are written only into the
output directory given on the command line.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import platform
import random
import subprocess
import sys

DRIVER_VERSION = 1

#: Prefix of the one stdout line carrying the driver's JSON result. Roam's imports can
#: print their own lines to stdout (e.g. pygame's banner), so the caller looks for this.
RESULT_PREFIX = "ROAM_USD_DRIVER_RESULT "

#: Roam's default room size (``gridSize: 17`` in Roam's config.yml at the pinned commit).
GRID_SIZE = 17

#: Roam's deepest cave level is z = -3 (``DEEPEST_Z`` in Roam's worldScreen.py).
MAX_CAVE_DEPTH = 3


def spiral_order(radius: int) -> list[tuple[int, int]]:
    """Every (x, y) with max(|x|, |y|) <= radius, in a fixed square spiral from (0, 0).

    The walk goes right, down (+y, Roam's screen-down), left, up, with run lengths
    1, 1, 2, 2, 3, 3, ...; cells outside the square are skipped.
    """
    if radius < 0:
        raise ValueError("radius must be >= 0")
    total = (2 * radius + 1) ** 2
    order = [(0, 0)]
    x = y = 0
    directions = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    turn = 0
    run = 1
    while len(order) < total:
        for _ in range(2):
            dx, dy = directions[turn % 4]
            for _ in range(run):
                x += dx
                y += dy
                if max(abs(x), abs(y)) <= radius:
                    order.append((x, y))
            turn += 1
        run += 1
    return order


def room_file_name(x: int, y: int, z: int) -> str:
    """Roam's room file naming: ``room_x_y.json`` on the surface, ``room_x_y_z.json`` below."""
    return f"room_{x}_{y}.json" if z == 0 else f"room_{x}_{y}_{z}.json"


class _TickCounter:
    """Stand-in for Roam's TickCounter: a new world at tick 0, never saved."""

    def getTick(self):  # noqa: N802 - Roam's method name
        return 0


class _Config:
    """Stand-in for Roam's Config. Generation does not read it; it points only at ``out``."""

    def __init__(self, out_dir: str):
        self.pathToSaveDirectory = out_dir
        self.gridSize = GRID_SIZE

    def getRoomsDirectory(self):  # noqa: N802
        return os.path.join(self.pathToSaveDirectory, "rooms")

    def getRoomFilePath(self, x, y, z=0):  # noqa: N802
        return os.path.join(self.getRoomsDirectory(), room_file_name(x, y, z))


def _git_commit(checkout: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", checkout, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _roam_version(checkout: str) -> str:
    try:
        with open(os.path.join(checkout, "version.txt"), encoding="utf-8") as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _has_entity(room, class_name: str) -> bool:
    grid = room.getGrid()
    for location_id in grid.getLocations():
        location = grid.getLocation(location_id)
        for entity in location.getEntities().values():
            if entity.__class__.__name__ == class_name:
                return True
    return False


def _import_roam(checkout: str):
    src = os.path.join(checkout, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from rendering.nullRenderer import NullRenderer
    from world.map import Map
    from world.roomJsonReaderWriter import RoomJsonReaderWriter
    from world.startingHomeGenerator import StartingHomeGenerator

    return Map, NullRenderer, RoomJsonReaderWriter, StartingHomeGenerator


def generate(
    checkout: str, seed: int, radius: int, cave_depth: int, home: bool, out_dir: str
) -> dict:
    """Generate one world into ``out_dir``; returns the manifest that was written."""
    if not 0 <= cave_depth <= MAX_CAVE_DEPTH:
        raise ValueError(f"cave depth must be between 0 and {MAX_CAVE_DEPTH}")
    Map, NullRenderer, RoomJsonReaderWriter, StartingHomeGenerator = _import_roam(checkout)

    # Until Roam has a per-world seed (roam#600), everything draws from the global
    # `random` module, so seeding it makes single-threaded, fixed-order generation
    # reproducible. If the checkout's Map accepts a `seed`, pass it as well.
    random.seed(seed)
    map_kwargs = {}
    seed_mechanism = "global-random"
    if "seed" in inspect.signature(Map.__init__).parameters:
        map_kwargs["seed"] = seed
        seed_mechanism = "roam-seed"

    renderer = NullRenderer()
    ticks = _TickCounter()
    config = _Config(out_dir)
    world_map = Map(GRID_SIZE, renderer, ticks, config, **map_kwargs)

    generated = []  # (x, y, z, room) in generation order
    for x, y in spiral_order(radius):
        generated.append((x, y, 0, world_map.generateNewRoom(x, y, 0)))

    if home:
        StartingHomeGenerator().generate(generated[0][3])

    for depth in range(1, cave_depth + 1):
        above = [entry for entry in generated if entry[2] == -(depth - 1)]
        for x, y, _, room in above:
            if _has_entity(room, "CaveEntrance"):
                generated.append((x, y, -depth, world_map.generateNewRoom(x, y, -depth)))

    writer = RoomJsonReaderWriter(GRID_SIZE, renderer, ticks, config)
    rooms_dir = config.getRoomsDirectory()
    os.makedirs(rooms_dir, exist_ok=True)
    room_types = {}
    for x, y, z, room in generated:
        with open(os.path.join(rooms_dir, room_file_name(x, y, z)), "w", encoding="utf-8") as f:
            json.dump(writer.generateJsonForRoom(room), f, indent=1)
        room_type = room.getRoomType()
        room_types[f"{x},{y},{z}"] = str(room_type) if room_type is not None else "unknown"

    manifest = {
        "format": "roam-usd-manifest",
        "driverVersion": DRIVER_VERSION,
        "seed": seed,
        "seedMechanism": seed_mechanism,
        "radius": radius,
        "caveDepth": cave_depth,
        "home": home,
        "gridSize": GRID_SIZE,
        "generationOrder": [[x, y, z] for x, y, z, _ in generated],
        "roomTypes": room_types,
        "roamCommit": _git_commit(checkout),
        "roamVersion": _roam_version(checkout),
        "pythonVersion": platform.python_version(),
    }
    with open(os.path.join(out_dir, "roam-usd-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return manifest


def list_classes(checkout: str) -> list[str]:
    """Entity class names Roam's room loader can construct at this checkout.

    Discovered at run time from the checkout (never copied): the loader's constructor
    table and living-entity registry, plus every class defined under ``src/entity``
    that the loader's own ``_createEntity`` turns into an instance of that class (this
    picks up its special cases, such as crops or NPCs). Used by the integration tests to
    check the catalog without depending on what a sample world happens to contain.
    """
    _, NullRenderer, RoomJsonReaderWriter, _ = _import_roam(checkout)
    from entity.living.livingEntityRegistry import LIVING_ENTITY_TYPES

    writer = RoomJsonReaderWriter(GRID_SIZE, NullRenderer(), _TickCounter(), _Config(""))
    found = set(writer.entityConstructors) | set(LIVING_ENTITY_TYPES)

    create = getattr(writer, "_createEntity", None)
    if create is not None:
        sample = {"tickCreated": 0, "tickPlanted": 0, "tickLastReproduced": 0, "energy": 1}
        for name in sorted(_entity_class_names(checkout) - found):
            try:
                entity = create(name, dict(sample))
            except Exception:  # noqa: BLE001 - a class the loader cannot build
                continue
            if entity is not None and type(entity).__name__ == name:
                found.add(name)
    return sorted(found)


def _entity_class_names(checkout: str) -> set[str]:
    """Names of the classes defined in the modules under ``<checkout>/src/entity``."""
    src = os.path.join(checkout, "src")
    names: set[str] = set()
    for root, _dirs, files in os.walk(os.path.join(src, "entity")):
        for file in sorted(files):
            if not file.endswith(".py") or file == "__init__.py":
                continue
            rel = os.path.relpath(os.path.join(root, file[:-3]), src)
            module_name = rel.replace(os.sep, ".")
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001 - skip modules that do not import
                continue
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    names.add(name)
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--out", help="output save directory")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--cave-depth", type=int, default=MAX_CAVE_DEPTH)
    parser.add_argument("--no-home", action="store_true")
    parser.add_argument("--list-classes", action="store_true")
    args = parser.parse_args(argv)

    checkout = os.path.abspath(args.checkout)
    if args.list_classes:
        print(RESULT_PREFIX + json.dumps(list_classes(checkout)), flush=True)
        return 0
    if args.out is None or args.seed is None:
        parser.error("--out and --seed are required")
    os.makedirs(args.out, exist_ok=True)
    manifest = generate(
        checkout, args.seed, args.radius, args.cave_depth, not args.no_home, args.out
    )
    print(RESULT_PREFIX + json.dumps({"rooms": len(manifest["generationOrder"])}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
