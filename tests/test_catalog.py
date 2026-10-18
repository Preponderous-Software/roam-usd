import logging
import re
from pathlib import Path

import pytest

from roam_usd import catalog

ARCHITECTURE = Path(__file__).resolve().parents[1] / "ARCHITECTURE.md"


def mapping_table_classes() -> set[str]:
    """Every backticked class name in the first column of ARCHITECTURE.md's mapping table."""
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("## Roam → USD mapping", 1)[1].split("\n## ", 1)[0]
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| ---"):
            continue
        first_cell = line.split("|")[1]
        names.update(re.findall(r"`([A-Z][A-Za-z]+)`", first_cell))
    return names


@pytest.fixture(autouse=True)
def fresh_warnings():
    catalog.reset_warnings()
    yield
    catalog.reset_warnings()


def test_mapping_table_is_parsed():
    names = mapping_table_classes()
    # Sanity: the parser found every group in the table, not an empty set.
    assert {"Grass", "Stone", "OakWood", "Fence", "Apple", "Chest", "Bear", "Npc"} <= names
    assert len(names) == 38


def test_every_mapping_table_class_has_an_entry():
    assert catalog.missing_classes(mapping_table_classes()) == []


def test_catalog_has_nothing_outside_the_mapping_table():
    assert set(catalog.CATALOG) == mapping_table_classes()


@pytest.mark.parametrize(
    "names, category",
    [
        (["Grass", "CaveFloor", "WoodFloor", "StoneFloor"], catalog.GROUND),
        (["Stone", "CoalOre", "IronOre", "GoldOre"], catalog.SOLID),
        (["OakWood", "JungleWood", "Leaves"], catalog.FLORA),
        (
            [
                "Fence",
                "Bed",
                "StoneBed",
                "Campfire",
                "Torch",
                "GoldenLantern",
                "Gravestone",
                "CaveEntrance",
                "CaveLadder",
            ],
            catalog.STRUCTURE,
        ),
        (
            ["Apple", "Banana", "ChickenMeat", "BearMeat", "Wheat", "WheatSeed", "Excrement"],
            catalog.ITEM,
        ),
        (["Chest", "IronChest", "YoungCrop", "MatureCrop"], catalog.STATEFUL),
        (["Bear", "Chicken", "Deer", "Rabbit", "Snake", "Wolf", "Npc"], catalog.CREATURE),
    ],
)
def test_categories(names, category):
    for name in names:
        assert catalog.lookup(name).category == category


def test_entries_are_well_formed():
    for entry in catalog.CATALOG.values():
        assert entry.category in catalog.CATEGORIES
        assert all(0.0 <= c <= 1.0 for c in entry.color)
        assert 0.0 < entry.width <= 1.0
        assert entry.height >= 0.0
        assert entry.individual == (entry.category in (catalog.STATEFUL, catalog.CREATURE))


def test_shapes_follow_the_mapping():
    assert catalog.lookup("Grass").height == pytest.approx(0.1)
    assert catalog.lookup("Stone").height == pytest.approx(1.0)
    assert catalog.lookup("Apple").width == pytest.approx(0.3)
    assert catalog.lookup("Leaves").lift > 0
    assert catalog.lookup("OakWood").height > catalog.lookup("OakWood").width
    assert catalog.lookup("Bear").shape == "card"


def test_unknown_class_returns_fallback_and_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="roam_usd.catalog"):
        first = catalog.lookup("Glowshroom")
        second = catalog.lookup("Glowshroom")
        catalog.lookup("Moonstone")
    assert first is second is catalog.FALLBACK
    assert first.color == (1.0, 0.0, 1.0)
    messages = [r.getMessage() for r in caplog.records]
    assert sum("Glowshroom" in m for m in messages) == 1
    assert sum("Moonstone" in m for m in messages) == 1


def test_known_class_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="roam_usd.catalog"):
        catalog.lookup("Grass")
    assert caplog.records == []


def test_reset_warnings_starts_a_new_run(caplog):
    with caplog.at_level(logging.WARNING, logger="roam_usd.catalog"):
        catalog.lookup("Glowshroom")
        catalog.reset_warnings()
        catalog.lookup("Glowshroom")
    assert len(caplog.records) == 2


def test_missing_classes():
    assert catalog.missing_classes(["Grass", "Zebra", "Apple", "Zebra", "Aardvark"]) == [
        "Aardvark",
        "Zebra",
    ]
    assert catalog.missing_classes([]) == []
    assert catalog.is_known("Grass") and not catalog.is_known("Zebra")
