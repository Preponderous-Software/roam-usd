# MVP

Status: **in development** — milestones land in order (M1 first). See [CONCEPT.md](./CONCEPT.md) for why and [ARCHITECTURE.md](./ARCHITECTURE.md) for how.

## The smallest end-to-end slice

> A Roam save folder — or a seed plus a local Roam checkout — goes in; a `.usda` stage comes out that opens in `usdview` and Blender 4.1+ and shows every saved room as a grid of instanced tiles, with creatures, structures, items, cave levels and the player in the right places.

Concretely, these two commands work:

```
roam-usd export ./roam/saves/defaultsavefile -o world.usda
roam-usd generate --roam ./roam --seeds 1..9 --radius 2 -o out/
```

The second writes `out/seed_1.usda` … `out/seed_9.usda` and `out/index.usda`, which shows all nine worlds side by side.

## In scope

- Reading Roam save directories: `rooms/room_<x>_<y>.json`, `rooms/room_<x>_<y>_<z>.json`, `playerLocation.json`, `tick.json`.
- Reading browser save bundles (`roam-saves-<date>.json`) and selecting one world from them.
- A hand-written entity catalog covering every entity class Roam's room loader knows at the pinned commit, plus a fallback for unknown classes.
- Writing a USD stage (`.usda` by default, `.usdc` with `--binary`): one `PointInstancer` per room for tiles, ground quads coloured by room background, sprite cards for creatures (textured from the user's Roam checkout when one is given), individual prims for stateful structures and the player, cave levels stacked below the surface in the same stage (4 units per level), metadata as `customData` / `customLayerData`.
- Room biome from a saved `roomType` field when present ([roam#601](https://github.com/Preponderous-Software/roam/issues/601)), `unknown` otherwise.
- Seeded generation by driving Roam's own generator in a subprocess from a user-supplied Roam checkout, in a fixed order, with a manifest; batch seeds plus an `index.usda` contact sheet.
- `roam-usd info` for a quick text summary without USD.
- Tests (unit, golden, stage validity, Roam integration at a pinned commit) and CI.

## Out of scope (for the MVP)

- Textures from Roam's PNGs for tile prototypes (`--textures`) — designed in ARCHITECTURE.md, built after the MVP. (Creature sprite cards are in scope.)
- Inferring a biome from a room's contents: saves without a `roomType` field show the biome as `unknown`.
- Player inventory, stats, goals, codex, achievements.
- Live export from Roam's REST API.
- USD → Roam import, or any write into a Roam save folder.
- Animation / time samples (e.g. day-night cycle, creature movement).
- `.usdz` packaging, materials beyond `displayColor` (other than the creature sprite-card material), LODs.
- Publishing to PyPI (installation is `pip install git+…` or from a clone until the format settles).
- Any change to Roam itself. (Two upstream proposals are filed as Roam issues; see [ARCHITECTURE.md § Decisions](./ARCHITECTURE.md#decisions).)

## Acceptance criteria

The MVP is done when all of these hold:

1. `pip install .` in a clean Python 3.10–3.13 environment installs `roam-usd` and its only runtime dependency, `usd-core`.
2. `roam-usd export <dir>` on the repository's hand-written fixture save produces a stage where:
   - every room file appears as exactly one `Room_<x>_<y>` prim under the correct level prim;
   - for every room, the number of instances of each prototype equals the number of entities of that class in the room file (excluding `Player` and the individually-modelled classes);
   - every creature, chest, crop and the player is an individual prim at its tile's coordinates;
   - a tile at room (rx, ry), tile (tx, ty) on a 17×17 grid is centred at X = 17·rx + tx + 0.5, Y = −(17·ry + ty + 0.5);
   - OpenUSD's validation framework (`pxr.UsdValidation`, the successor of `UsdUtils.ComplianceChecker`, which usd-core 26.x no longer ships) reports no errors.
3. Exporting the same input twice produces byte-identical `.usda`.
4. An unknown `entityClass` in a room file does not stop the export; it is rendered as the fallback prototype and reported once as a warning.
5. A browser save bundle containing two worlds can be listed (`roam-usd info bundle.json`) and either world exported with `--world`.
6. With Roam checked out at the pinned commit, `roam-usd generate --seed 1` run twice yields byte-identical stages, and `--seed 2` yields a different one; the generated save directory also exports cleanly through `roam-usd export`.
7. `generate --seeds 1..4 --radius 1` writes four per-seed stages and an `index.usda` that references them without overlap.
8. CI runs the unit/golden tests on every push and the Roam integration job on every push to `main`.
9. A generated world and a fixture save open in `usdview` and in Blender 4.1+ (import as USD), showing ground, tiles, creatures and cave levels; screenshots are in the README.
10. No file in the repository is copied from Roam (source, schemas, assets or real saves), and `pyproject.toml` does not depend on Roam.

## Milestones

Each milestone maps 1:1 to a GitHub issue labelled `mvp`.

### M1 — Project skeleton and CI

Tracked in [#1](https://github.com/Preponderous-Software/roam-usd/issues/1).

Package layout (`src/roam_usd/`), `pyproject.toml` with `usd-core>=26.3,<27`, the `roam-usd` console script with `export`, `generate` and `info` subcommands as stubs, `pytest` + `ruff` configuration, and a GitHub Actions workflow running lint and tests on Python 3.10–3.13.

### M2 — Save reader and WorldModel

Tracked in [#2](https://github.com/Preponderous-Software/roam-usd/issues/2).

`roam_usd.save` reads a save directory into plain dataclasses (`World`, `Level`, `Room`, `Tile`, `EntityRecord`, `PlayerMarker`), following the rules in [ARCHITECTURE.md § Save reader](./ARCHITECTURE.md#1-save-reader-roam_usdsave). Hand-written fixtures cover surface and cave rooms, missing `z`, rooms with and without `roomType`, negative coordinates, a non-17 grid, unknown classes, `Player` records and a malformed file. `roam-usd info` prints rooms per level and entity counts per class.

### M3 — Entity catalog

Tracked in [#3](https://github.com/Preponderous-Software/roam-usd/issues/3).

`roam_usd.catalog` assigns every known `entityClass` a category (ground, solid, flora, structure, item, stateful structure, creature), a prototype shape, height and colour, per the [mapping table](./ARCHITECTURE.md#roam--usd-mapping). Includes the fallback entry and a test that the catalog covers a given class list.

### M4 — USD stage writer: levels, rooms, ground and instanced tiles

Tracked in [#4](https://github.com/Preponderous-Software/roam-usd/issues/4).

`roam_usd.usd` writes the stage hierarchy (`/World`, level prims, `Room_<x>_<y>`), ground quads coloured from `backgroundColor`, shared prototypes under `/World/Prototypes`, and one `PointInstancer` per room with `protoIndices`, `positions` and coordinate-derived `ids`; entity stacks rise in file order. Level prims stacked 4 units apart on Z in the same stage. Room prims carry `roam:roomType` from the room file when present, else `unknown`. Stage metadata (`upAxis`, `metersPerUnit`, `customLayerData`). Deterministic `.usda` output by default and `.usdc` with `--binary`, golden tests, compliance check. `roam-usd export` works end to end for tiles.

### M5 — Creatures, stateful structures and the player

Tracked in [#5](https://github.com/Preponderous-Software/roam-usd/issues/5).

Sprite cards for creatures (`Bear`, `Chicken`, `Deer`, `Rabbit`, `Snake`, `Wolf`, `Npc`) — flat quads textured with the sprite named by each creature's `imagePath`, read from the user's Roam checkout (`--roam`) at export time and left untextured without one — individual prims for stateful structures (`Chest`, `IronChest`, `YoungCrop`, `MatureCrop`) and the player marker (from `playerLocation.json`, honouring `roomZ`), with Roam fields carried as `customData["roam:*"]` and chest contents summarised by class and count.

### M6 — Seeded generation driver

Tracked in [#6](https://github.com/Preponderous-Software/roam-usd/issues/6).

`roam-usd generate --roam <checkout> --seed N` runs `roam_usd/_driver.py` in a subprocess with the checkout as working directory: seeds `random`, generates rooms in a fixed spiral order within `--radius` using Roam's `Map` and `NullRenderer`, builds the starting home (unless `--no-home`), descends into caves under rooms with a `CaveEntrance` down to `--cave-depth`, writes rooms with Roam's `RoomJsonReaderWriter.generateJsonForRoom`, and writes `roam-usd-manifest.json`. The exporter reads the manifest for seed, Roam commit and, where the room files lack it, `roomType`. Once Roam has a per-world seed ([roam#600](https://github.com/Preponderous-Software/roam/issues/600)), the driver uses it instead of seeding `random`. CI integration job clones Roam at the pinned SHA and checks determinism.

### M7 — Batch seeds and contact sheet

Tracked in [#7](https://github.com/Preponderous-Software/roam-usd/issues/7).

`--seeds A..B` (and comma lists) generate and export one stage per seed, then write `index.usda` referencing each seed's stage, laid out in a square grid with spacing derived from the largest world, each labelled with its seed in `customData`.

### M8 — Browser save bundles, viewer verification and docs

Tracked in [#8](https://github.com/Preponderous-Software/roam-usd/issues/8).

`roam_usd.save` accepts a `roam-saves` bundle (validates `format`/`version`, lists worlds, decodes the selected world's files in memory). README gains install/usage sections, the license note for `generate`, and `usdview` + Blender screenshots of a fixture world and a generated world; a headless Blender import smoke test runs in CI if Blender is available, otherwise a documented manual checklist.
