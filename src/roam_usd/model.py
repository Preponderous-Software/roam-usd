"""The in-memory world model: plain dataclasses, independent of Roam and of USD.

The save reader (:mod:`roam_usd.save`) fills these in; the USD writer reads them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

UNKNOWN_ROOM_TYPE = "unknown"


@dataclass(frozen=True)
class EntityRecord:
    """One entity as recorded in a room file.

    ``stack_index`` is the entity's position in its location's ``entities`` list;
    order drives vertical stacking. ``data`` is the full record from the save,
    including class-specific fields (``energy``, ``tickPlanted``, ...).
    """

    entity_class: str
    stack_index: int
    data: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def id(self) -> str | None:
        value = self.data.get("id")
        return None if value is None else str(value)


@dataclass(frozen=True)
class Tile:
    """A grid location: integer ``x`` / ``y`` within its room and its entity stack."""

    x: int
    y: int
    location_id: str
    entities: tuple[EntityRecord, ...]


@dataclass(frozen=True)
class Room:
    x: int
    y: int
    z: int
    columns: int
    rows: int
    background_color: tuple[int, int, int]
    room_type: str
    name: str
    tiles: tuple[Tile, ...]
    source_file: str = ""

    def entity_counts(self) -> Counter[str]:
        return Counter(e.entity_class for t in self.tiles for e in t.entities)

    def tile_by_location_id(self, location_id: str) -> Tile | None:
        for tile in self.tiles:
            if tile.location_id == location_id:
                return tile
        return None


def level_name(z: int) -> str:
    """Prim-safe level name: ``Surface`` for z = 0, ``Cave_<n>`` for z = -n."""
    if z == 0:
        return "Surface"
    if z < 0:
        return f"Cave_{-z}"
    return f"Level_{z}"


@dataclass(frozen=True)
class Level:
    z: int
    rooms: tuple[Room, ...]

    @property
    def name(self) -> str:
        return level_name(self.z)


@dataclass(frozen=True)
class PlayerMarker:
    room_x: int
    room_y: int
    room_z: int
    location_id: str


@dataclass(frozen=True)
class World:
    """A whole save: levels ordered from the surface downwards."""

    levels: tuple[Level, ...]
    player: PlayerMarker | None = None
    tick: int | None = None
    source: str = ""
    manifest: dict[str, Any] | None = field(default=None, compare=False)

    def rooms(self) -> list[Room]:
        return [room for level in self.levels for room in level.rooms]

    def find_room(self, x: int, y: int, z: int) -> Room | None:
        for room in self.rooms():
            if (room.x, room.y, room.z) == (x, y, z):
                return room
        return None

    def entity_counts(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for room in self.rooms():
            total.update(room.entity_counts())
        return total
