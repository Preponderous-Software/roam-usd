"""Read a Roam save into a :class:`~roam_usd.model.World`.

This is an independent implementation of the save layout described in
ARCHITECTURE.md § Save reader. It uses only the standard library and never
imports Roam.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from roam_usd.model import (
    UNKNOWN_ROOM_TYPE,
    EntityRecord,
    Level,
    PlayerMarker,
    Room,
    Tile,
    World,
)

log = logging.getLogger(__name__)

ROOM_FILE_RE = re.compile(r"^room_(-?\d+)_(-?\d+)(?:_(-?\d+))?\.json$")

# Roam's loader skips these records; the player marker comes from playerLocation.json.
SKIPPED_CLASSES = frozenset({"Player"})


class SaveFormatError(ValueError):
    """A save file is missing a field or has an invalid value. The message names the file."""


def parse_background_color(value: Any, where: str = "backgroundColor") -> tuple[int, int, int]:
    """Parse ``"(203, 134, 61)"`` (or a 3-item list) into an RGB tuple of ints 0..255."""
    if isinstance(value, str):
        parts = value.strip().strip("()[]").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise SaveFormatError(f"{where}: invalid backgroundColor {value!r}")
    try:
        rgb = tuple(int(str(p).strip()) for p in parts)
    except ValueError:
        raise SaveFormatError(f"{where}: invalid backgroundColor {value!r}") from None
    if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
        raise SaveFormatError(f"{where}: invalid backgroundColor {value!r}")
    return rgb  # type: ignore[return-value]


def _require(obj: Any, key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise SaveFormatError(f"{where}: missing field '{key}'")
    value = obj[key]
    # bool is an int subclass; never accept it where an int is meant.
    if not isinstance(value, kind) or (isinstance(value, bool) and bool not in _as_tuple(kind)):
        raise SaveFormatError(f"{where}: invalid field '{key}': {value!r}")
    return value


def _as_tuple(kind: type | tuple[type, ...]) -> tuple[type, ...]:
    return kind if isinstance(kind, tuple) else (kind,)


def _load_json(read: Callable[[], str], where: str) -> Any:
    """Read and parse one save file, reporting any failure as a SaveFormatError on ``where``."""
    try:
        text = read()
    except UnicodeDecodeError as exc:
        raise SaveFormatError(f"{where}: not valid UTF-8 ({exc.reason})") from None
    except OSError as exc:
        raise SaveFormatError(f"{where}: cannot be read ({exc.strerror or exc})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SaveFormatError(f"{where}: not valid JSON ({exc})") from None


def parse_room(data: Any, where: str, filename_coords: tuple[int, int, int] | None = None) -> Room:
    """Build a :class:`Room` from one parsed room file."""
    if not isinstance(data, dict):
        raise SaveFormatError(f"{where}: a room file must hold a JSON object")
    x = _require(data, "x", int, where)
    y = _require(data, "y", int, where)
    z = data.get("z", 0)
    if not isinstance(z, int) or isinstance(z, bool):
        raise SaveFormatError(f"{where}: invalid field 'z': {z!r}")
    color = parse_background_color(_require(data, "backgroundColor", (str, list), where), where)
    # An absent roomType (every save before roam#601) means "unknown". A null is read
    # the same way, since Roam's in-memory Room.roomType defaults to None; any other
    # non-string or empty value is malformed.
    room_type = data.get("roomType")
    if room_type is None:
        room_type = UNKNOWN_ROOM_TYPE
    elif not isinstance(room_type, str) or not room_type.strip():
        raise SaveFormatError(f"{where}: invalid field 'roomType': {room_type!r}")
    name = data.get("name")
    name = name if isinstance(name, str) else f"({x}, {y})"

    grid = _require(data, "grid", dict, where)
    columns = _require(grid, "columns", int, f"{where}: grid")
    rows = _require(grid, "rows", int, f"{where}: grid")
    if columns <= 0 or rows <= 0:
        raise SaveFormatError(f"{where}: grid size must be positive, got {columns}x{rows}")
    locations = _require(grid, "locations", list, f"{where}: grid")

    tiles = []
    seen: set[tuple[int, int]] = set()
    for i, loc in enumerate(locations):
        loc_where = f"{where}: grid.locations[{i}]"
        tx = _require(loc, "x", int, loc_where)
        ty = _require(loc, "y", int, loc_where)
        if not (0 <= tx < columns and 0 <= ty < rows):
            raise SaveFormatError(f"{loc_where}: tile ({tx}, {ty}) is outside the grid")
        if (tx, ty) in seen:
            raise SaveFormatError(f"{loc_where}: duplicate tile ({tx}, {ty})")
        seen.add((tx, ty))
        raw_entities = _require(loc, "entities", list, loc_where)
        entities = []
        for k, ent in enumerate(raw_entities):
            entity_class = _require(ent, "entityClass", str, f"{loc_where}.entities[{k}]")
            if entity_class in SKIPPED_CLASSES:
                continue
            entities.append(EntityRecord(entity_class, k, dict(ent)))
        tiles.append(Tile(tx, ty, str(loc.get("id", "")), tuple(entities)))
    tiles.sort(key=lambda t: (t.y, t.x))

    if filename_coords is not None and filename_coords != (x, y, z):
        log.warning(
            "%s: coordinates in the file (%d, %d, %d) disagree with its name %s; "
            "using the file contents",
            where,
            x,
            y,
            z,
            filename_coords,
        )
    return Room(x, y, z, columns, rows, color, room_type, name, tuple(tiles), where)


def parse_player_location(data: Any, where: str) -> PlayerMarker:
    room_x = _require(data, "roomX", int, where)
    room_y = _require(data, "roomY", int, where)
    room_z = data.get("roomZ", 0)
    if not isinstance(room_z, int) or isinstance(room_z, bool):
        raise SaveFormatError(f"{where}: invalid field 'roomZ': {room_z!r}")
    location_id = _require(data, "locationId", str, where)
    return PlayerMarker(room_x, room_y, room_z, location_id)


def parse_tick(data: Any, where: str) -> int:
    return _require(data, "tick", int, where)


def build_world(
    room_files: Iterable[tuple[str, Callable[[], str]]],
    player_text: Callable[[], str] | None,
    tick_text: Callable[[], str] | None,
    source: str,
) -> World:
    """Assemble a :class:`World` from save files given as (name, reader) pairs.

    ``room_files`` holds the bare file names inside ``rooms/``. Shared by the
    directory reader and the browser-bundle reader.
    """
    rooms: dict[tuple[int, int, int], Room] = {}
    for name, read in sorted(room_files, key=lambda item: item[0]):
        match = ROOM_FILE_RE.match(name)
        if not match:
            log.warning("%s: ignoring file that is not a room file: %s", source, name)
            continue
        fx, fy, fz = match.groups()
        filename_coords = (int(fx), int(fy), int(fz) if fz is not None else 0)
        where = f"{source}/rooms/{name}" if source else f"rooms/{name}"
        room = parse_room(_load_json(read, where), where, filename_coords)
        key = (room.x, room.y, room.z)
        if key in rooms:
            raise SaveFormatError(f"{where}: room {key} is also stored in {rooms[key].source_file}")
        rooms[key] = room

    by_level: dict[int, list[Room]] = {}
    for room in rooms.values():
        by_level.setdefault(room.z, []).append(room)
    levels = tuple(
        Level(z, tuple(sorted(by_level[z], key=lambda r: (r.y, r.x))))
        for z in sorted(by_level, reverse=True)
    )

    player = None
    if player_text is not None:
        where = f"{source}/playerLocation.json"
        player = parse_player_location(_load_json(player_text, where), where)
    tick = None
    if tick_text is not None:
        where = f"{source}/tick.json"
        tick = parse_tick(_load_json(tick_text, where), where)
    return World(levels=levels, player=player, tick=tick, source=source)


def _reader(path: Path) -> Callable[[], str]:
    return lambda: path.read_text(encoding="utf-8")


def read_save_dir(path: str | Path, source: str | None = None) -> World:
    """Read a Roam save directory (``rooms/``, ``playerLocation.json``, ``tick.json``)."""
    root = Path(path)
    label = str(path) if source is None else source
    if not root.is_dir():
        raise SaveFormatError(f"{label}: not a directory")
    rooms_dir = root / "rooms"
    if not rooms_dir.is_dir():
        raise SaveFormatError(f"{label}: no rooms/ directory; is this a Roam save folder?")
    room_files = [(p.name, _reader(p)) for p in rooms_dir.iterdir() if p.is_file()]
    player = root / "playerLocation.json"
    tick = root / "tick.json"
    return build_world(
        room_files,
        _reader(player) if player.is_file() else None,
        _reader(tick) if tick.is_file() else None,
        label,
    )


def format_info(world: World) -> str:
    """The text ``roam-usd info`` prints: tick, rooms per level, entity counts, player."""
    lines = [f"World: {world.source}"]
    lines.append(f"Tick: {world.tick if world.tick is not None else 'unknown'}")
    rooms = world.rooms()
    lines.append(f"Rooms: {len(rooms)}")
    for level in world.levels:
        n = len(level.rooms)
        lines.append(f"  {level.name} (z={level.z}): {n} room{'s' if n != 1 else ''}")
    counts = world.entity_counts()
    lines.append(f"Entities: {sum(counts.values())}")
    width = max((len(name) for name in counts), default=0)
    for name in sorted(counts):
        lines.append(f"  {name:<{width}}  {counts[name]}")
    if world.player is None:
        lines.append("Player: not recorded")
    else:
        p = world.player
        where = f"room ({p.room_x}, {p.room_y}, {p.room_z})"
        room = world.find_room(p.room_x, p.room_y, p.room_z)
        tile = room.tile_by_location_id(p.location_id) if room else None
        if tile is not None:
            where += f", tile ({tile.x}, {tile.y})"
        else:
            where += ", tile not found"
        lines.append(f"Player: {where}")
    return "\n".join(lines)


# --- browser save bundles (M8) ------------------------------------------------------------

BUNDLE_FORMAT = "roam-saves"
BUNDLE_VERSION = 1
_BUNDLE_ROOT = "/saves/"


def _bundle_text(value: Any, where: str) -> str:
    """A bundle file's content: JSON saves are strings, binary files ``{"base64": ...}``."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("base64"), str):
        try:
            return base64.b64decode(value["base64"], validate=True).decode("utf-8")
        except (binascii.Error, ValueError):
            raise SaveFormatError(f"{where}: invalid base64 content") from None
    raise SaveFormatError(f'{where}: content must be a string or {{"base64": ...}}')


def read_bundle(path: str | Path) -> dict[str, dict[str, Any]]:
    """Validate a browser save download and return ``{world: {relative path: content}}``."""
    label = str(path)
    data = _load_json(_reader(Path(path)), label)
    if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
        raise SaveFormatError(f"{label}: not a Roam save bundle (format is not {BUNDLE_FORMAT!r})")
    version = data.get("version")
    if version != BUNDLE_VERSION:
        raise SaveFormatError(
            f"{label}: unsupported bundle version {version!r} (supported: {BUNDLE_VERSION})"
        )
    files = data.get("files")
    if not isinstance(files, dict):
        raise SaveFormatError(f"{label}: missing field 'files'")
    worlds: dict[str, dict[str, Any]] = {}
    for key, value in files.items():
        if not isinstance(key, str) or not key.startswith(_BUNDLE_ROOT):
            raise SaveFormatError(f"{label}: file path {key!r} is not under {_BUNDLE_ROOT}")
        world, _, rel = key[len(_BUNDLE_ROOT) :].partition("/")
        if not world or not rel:
            raise SaveFormatError(f"{label}: file path {key!r} names no world file")
        worlds.setdefault(world, {})[rel] = value
    if not worlds:
        raise SaveFormatError(f"{label}: the bundle holds no worlds")
    return dict(sorted(worlds.items()))


def bundle_world_names(path: str | Path) -> list[str]:
    return list(read_bundle(path))


def read_bundle_world(path: str | Path, world: str | None = None) -> World:
    """Read one world from a bundle, in memory, through the same code path as a directory."""
    label = str(path)
    worlds = read_bundle(path)
    names = ", ".join(worlds)
    if world is None:
        if len(worlds) > 1:
            raise SaveFormatError(
                f"{label}: holds {len(worlds)} worlds ({names}); choose one with --world"
            )
        world = next(iter(worlds))
    elif world not in worlds:
        raise SaveFormatError(f"{label}: no world {world!r} (worlds: {names})")
    files = worlds[world]
    source = f"{label}#{world}"

    def entry(rel: str) -> Callable[[], str]:
        return lambda: _bundle_text(files[rel], f"{source}/{rel}")

    room_files = [
        (rel[len("rooms/") :], entry(rel))
        for rel in files
        if rel.startswith("rooms/") and "/" not in rel[len("rooms/") :]
    ]
    if not room_files:
        raise SaveFormatError(f"{source}: no rooms/ files in this world")
    player = entry("playerLocation.json") if "playerLocation.json" in files else None
    tick = entry("tick.json") if "tick.json" in files else None
    return build_world(room_files, player, tick, source)


def read_save(path: str | Path, world: str | None = None) -> World:
    """A save folder, or (for a file) a browser save bundle with an optional ``world``."""
    if Path(path).is_dir():
        if world is not None:
            raise SaveFormatError(f"{path}: --world only applies to a browser save bundle")
        return read_save_dir(path)
    if Path(path).is_file():
        return read_bundle_world(path, world)
    raise SaveFormatError(f"{path}: no such save folder or bundle file")
