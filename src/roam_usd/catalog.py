"""The entity catalog: how each Roam ``entityClass`` looks in USD.

A table written for roam-usd (ARCHITECTURE.md § Roam → USD mapping). Colours are
chosen by hand for legibility; they are not sampled from Roam's art.

Every prototype is a box standing on its tile: ``width`` × ``width`` in X/Y,
``height`` tall, its base lifted ``lift`` above the point it is placed at. The next
entity in a tile's stack sits at ``lift + height`` above this one (``stack_height``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Categories
GROUND = "ground"
SOLID = "solid"
FLORA = "flora"
STRUCTURE = "structure"
ITEM = "item"
STATEFUL = "stateful structure"
CREATURE = "creature"
UNKNOWN = "unknown"

CATEGORIES = (GROUND, SOLID, FLORA, STRUCTURE, ITEM, STATEFUL, CREATURE, UNKNOWN)

# Categories rendered as individual prims rather than PointInstancer instances.
INDIVIDUAL_CATEGORIES = frozenset({STATEFUL, CREATURE})

Color = tuple[float, float, float]


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    category: str
    shape: str
    width: float
    height: float
    color: Color
    lift: float = 0.0

    @property
    def stack_height(self) -> float:
        """Height the next entity on the same tile starts at, relative to this one."""
        return self.lift + self.height

    @property
    def individual(self) -> bool:
        """True for classes written as individual prims (creatures, stateful structures)."""
        return self.category in INDIVIDUAL_CATEGORIES


def _entry(name, category, shape, width, height, color, lift=0.0) -> CatalogEntry:
    return CatalogEntry(name, category, shape, width, height, color, lift)


_ENTRIES = [
    # Terrain / ground cover: thin slabs.
    _entry("Grass", GROUND, "slab", 1.0, 0.1, (0.30, 0.62, 0.25)),
    _entry("CaveFloor", GROUND, "slab", 1.0, 0.1, (0.33, 0.30, 0.28)),
    _entry("WoodFloor", GROUND, "slab", 1.0, 0.1, (0.62, 0.45, 0.27)),
    _entry("StoneFloor", GROUND, "slab", 1.0, 0.1, (0.56, 0.56, 0.58)),
    # Solid terrain: unit cubes.
    _entry("Stone", SOLID, "cube", 1.0, 1.0, (0.50, 0.50, 0.52)),
    _entry("CoalOre", SOLID, "cube", 1.0, 1.0, (0.20, 0.20, 0.22)),
    _entry("IronOre", SOLID, "cube", 1.0, 1.0, (0.72, 0.55, 0.45)),
    _entry("GoldOre", SOLID, "cube", 1.0, 1.0, (0.90, 0.74, 0.18)),
    # Flora: trunks are tall narrow boxes, leaves a canopy slab raised above the ground.
    _entry("OakWood", FLORA, "trunk", 0.4, 1.5, (0.45, 0.30, 0.16)),
    _entry("JungleWood", FLORA, "trunk", 0.4, 1.5, (0.33, 0.23, 0.11)),
    _entry("Leaves", FLORA, "canopy", 1.0, 0.4, (0.18, 0.48, 0.16), lift=1.1),
    # Static structures.
    _entry("Fence", STRUCTURE, "post", 0.2, 0.8, (0.55, 0.40, 0.22)),
    _entry("Bed", STRUCTURE, "box", 0.8, 0.3, (0.72, 0.20, 0.20)),
    _entry("StoneBed", STRUCTURE, "box", 0.8, 0.3, (0.62, 0.62, 0.64)),
    _entry("Campfire", STRUCTURE, "box", 0.6, 0.25, (0.88, 0.36, 0.10)),
    _entry("Torch", STRUCTURE, "post", 0.12, 0.7, (0.96, 0.70, 0.20)),
    _entry("GoldenLantern", STRUCTURE, "box", 0.25, 0.4, (0.96, 0.82, 0.30)),
    _entry("Gravestone", STRUCTURE, "box", 0.5, 0.6, (0.45, 0.45, 0.48)),
    _entry("CaveEntrance", STRUCTURE, "slab", 0.8, 0.05, (0.04, 0.04, 0.05)),
    _entry("CaveLadder", STRUCTURE, "post", 0.6, 1.2, (0.55, 0.38, 0.20)),
    # Ground items: small cubes.
    _entry("Apple", ITEM, "small_cube", 0.3, 0.3, (0.85, 0.12, 0.12)),
    _entry("Banana", ITEM, "small_cube", 0.3, 0.3, (0.95, 0.85, 0.25)),
    _entry("ChickenMeat", ITEM, "small_cube", 0.3, 0.3, (0.95, 0.74, 0.64)),
    _entry("BearMeat", ITEM, "small_cube", 0.3, 0.3, (0.60, 0.20, 0.20)),
    _entry("Wheat", ITEM, "small_cube", 0.3, 0.3, (0.90, 0.80, 0.40)),
    _entry("WheatSeed", ITEM, "small_cube", 0.3, 0.3, (0.70, 0.60, 0.30)),
    _entry("Excrement", ITEM, "small_cube", 0.3, 0.3, (0.40, 0.28, 0.12)),
    # Stateful structures: individual prims.
    _entry("Chest", STATEFUL, "box", 0.7, 0.6, (0.60, 0.42, 0.20)),
    _entry("IronChest", STATEFUL, "box", 0.7, 0.6, (0.55, 0.56, 0.60)),
    _entry("YoungCrop", STATEFUL, "box", 0.5, 0.3, (0.50, 0.80, 0.30)),
    _entry("MatureCrop", STATEFUL, "box", 0.7, 0.6, (0.85, 0.75, 0.30)),
    # Creatures: flat one-tile sprite cards (zero height) on top of the tile's stack.
    _entry("Bear", CREATURE, "card", 1.0, 0.0, (0.40, 0.26, 0.13)),
    _entry("Chicken", CREATURE, "card", 1.0, 0.0, (0.95, 0.95, 0.90)),
    _entry("Deer", CREATURE, "card", 1.0, 0.0, (0.70, 0.50, 0.30)),
    _entry("Rabbit", CREATURE, "card", 1.0, 0.0, (0.80, 0.78, 0.72)),
    _entry("Snake", CREATURE, "card", 1.0, 0.0, (0.30, 0.55, 0.20)),
    _entry("Wolf", CREATURE, "card", 1.0, 0.0, (0.55, 0.55, 0.58)),
    _entry("Npc", CREATURE, "card", 1.0, 0.0, (0.30, 0.45, 0.80)),
]

CATALOG: dict[str, CatalogEntry] = {e.name: e for e in _ENTRIES}

#: Rendered for any class not in the catalog: a magenta cube.
FALLBACK = CatalogEntry("Fallback", UNKNOWN, "cube", 0.8, 0.8, (1.0, 0.0, 1.0))

_warned: set[str] = set()


def reset_warnings() -> None:
    """Forget which unknown classes were already reported (one run = one report each)."""
    _warned.clear()


def lookup(entity_class: str) -> CatalogEntry:
    """Return the entry for ``entity_class``, or :data:`FALLBACK` with a once-per-class warning."""
    entry = CATALOG.get(entity_class)
    if entry is not None:
        return entry
    if entity_class not in _warned:
        _warned.add(entity_class)
        log.warning("unknown entity class %r; rendering it as the fallback cube", entity_class)
    return FALLBACK


def is_known(entity_class: str) -> bool:
    return entity_class in CATALOG


def missing_classes(class_names: Iterable[str]) -> list[str]:
    """The names in ``class_names`` that have no catalog entry, sorted and de-duplicated."""
    return sorted({name for name in class_names if name not in CATALOG})
