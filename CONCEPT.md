# Concept

roam-usd turns worlds from the game [Roam](https://github.com/Preponderous-Software/roam) into [OpenUSD](https://openusd.org/) stages: every tile, creature, structure and item in a world becomes part of a 3D scene that opens in `usdview`, Blender, or any other USD-aware tool.

All claims about Roam in this document were checked against Roam's `main` branch at commit [`a8d9ad6`](https://github.com/Preponderous-Software/roam/tree/a8d9ad6bc819d02195d047b103b96eb56d55063c) (version `0.13.0-SNAPSHOT-8-8-2026`, committed 2026-10-16). Links below are permalinks to that commit; `R/` is shorthand for `https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/`.

## The problem

A Roam world can only be seen through Roam itself: the pygame window, the text/TUI frontend, the in-browser Pyodide build, or the stitched 2D minimap PNG the game writes into the save folder ([`R/src/mapimage/mapImageGenerator.py`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/mapimage/mapImageGenerator.py)). There is no way to:

- look at a whole explored world (every room, every cave level) at once, at any zoom or angle;
- inspect the world as data in standard tools — select a tile, see which entities are stacked on it, filter by entity class;
- render a world, or a batch of freshly generated worlds, outside the game;
- compare what the world generator produces for different random seeds side by side.

Roam's world is already structured, spatial data (a grid of rooms, each a grid of tiles, each tile a stack of entities), so it maps naturally onto a scene graph. USD is the interchange format built for exactly this kind of hierarchical, instanced, metadata-rich scene.

## Who it is for

- **Roam players** who want to see or render a world they explored, including the cave levels beneath it.
- **Roam's developers**, as a debugging and design aid: viewing many generated rooms at once makes the biome mix, cave cellular-automata output and creature spawn density visible at a glance.
- **People learning OpenUSD** who want a small, readable, real-world exporter: a 2D tile game is about the simplest non-trivial source of instanced geometry there is.

## Does Roam generate its world procedurally?

**Yes — but it is not seeded, and it is generated lazily, room by room, as the player explores.** Details, with evidence:

1. **The world is a grid of rooms, generated on demand.** `Map.generateNewRoom(x, y, z)` ([`R/src/world/map.py#L86-L106`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/map.py#L86-L106)) is called when the player first reaches a room that has no save file. Rooms are not pre-generated: the world only exists as far as it has been explored. A background `RoomPreloader` also generates neighbouring rooms on a thread pool ([`R/src/world/roomPreloader.py`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/roomPreloader.py)).
2. **Biomes chain from room to room.** For surface rooms (`z == 0`), there is a 50% chance the new room repeats the *previous* room type created (`roomFactory.lastRoomTypeCreated`), otherwise one of grassland / forest / jungle / mountain is picked uniformly ([`R/src/world/map.py#L93-L98`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/map.py#L93-L98), [`R/src/world/roomFactory.py#L66-L78`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/roomFactory.py#L66-L78)). The biome of a room therefore depends on the *order* in which rooms were generated, not just on its coordinates.
3. **Each room type is filled by random scattering.** `RoomFactory` ([`R/src/world/roomFactory.py`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/roomFactory.py)) places grass on ~95% of tiles, occasional stones, trees with leaves/apples/bananas around them, ore at ~35% density in mountains, creatures by independent spawn rolls, and cave entrances with a per-biome chance (5% grassland, 15% forest, 40% mountain).
4. **Caves (`z < 0`) use cellular automata.** `_generateCaveTiles` ([`R/src/world/roomFactory.py#L174-L240`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/roomFactory.py#L174-L240)) fills a grid with open cells at a depth-dependent probability, runs 4 passes of a Moore-neighbourhood rule, keeps the largest flood-filled region, scatters depth-dependent ore along the walls, and places a ladder (and sometimes a deeper entrance).
5. **There is no seed.** Every random draw uses the process-global `random` module (`import random` in `roomFactory.py`, `map.py`, and the vendored `R/src/lib/pyenvlib/grid.py`, whose `addEntity` picks a random tile). A repository-wide search for `random.seed` / `random.Random` in `src/` finds nothing, and `config.yml` has no seed setting. Entity, tile and grid IDs are `uuid.uuid4()` values.
6. **A brand-new world gets a fixed starting home** in room (0, 0), built deterministically (no random draws) by `StartingHomeGenerator` ([`R/src/world/startingHomeGenerator.py`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/world/startingHomeGenerator.py)), invoked from `WorldScreen`, not from `Map`.

**Consequence for this project.** Because all generation draws from the global `random` module, seeding it before calling Roam's own generator makes the output reproducible *as long as rooms are generated single-threaded and in a fixed order*. This was checked during planning: on Python 3.12, with Roam at `a8d9ad6`, calling `random.seed(1)` and then generating the same five rooms plus one cave room twice in a row produced byte-identical tile contents (same room types, background colours and per-tile entity stacks); `random.seed(2)` produced a different world. The in-game world is *not* reproducible this way, because the player's path and the preloader thread decide generation order — so a seed identifies a world *as generated by roam-usd*, not a world a player could re-create in the game.

That makes **"seed → world → USD" a real, headline feature**, with a stated scope: roam-usd can generate N worlds from N seeds by driving Roam's own generator in a fixed spiral order, and export each one. Exporting real saves stays the other half of the tool, and both halves meet at the same point: Roam's on-disk save format.

## What makes it interesting

- **Seeded batch export.** `roam-usd generate --seeds 1..16 --radius 3` produces sixteen 7×7-room worlds (each room 17×17 tiles by default, per `gridSize: 17` in [`R/config.yml`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/config.yml)) as USD stages that can be laid out side by side — a contact sheet for the world generator.
- **A real save, in 3D.** An explored world, its cave levels stacked beneath the surface at their real `z`, the player's position, creatures where they stood when the game saved.
- **Instancing that matches the data.** A 7×7-room world has 14,161 tiles, almost all of which are one of a handful of entity classes (Grass, Stone, Leaves, OakWood, CaveFloor...). One `UsdGeomPointInstancer` per room with one prototype per entity class keeps the stage small and fast to open.
- **Metadata survives.** Entity class, energy, ticks, chest contents and the source (save path, or seed + Roam commit) are carried as USD custom data, so the scene doubles as an inspectable dump of the world.

## Non-goals

- **Not a renderer or a game client.** roam-usd writes files; it does not draw anything or let anyone play.
- **No round trip.** USD → Roam save import is out of scope; roam-usd never writes into a Roam save folder.
- **No live streaming.** Roam has an opt-in, read-only REST API "for external client tools (e.g. a world viewer)" ([`R/src/api/restApiServer.py`](https://github.com/Preponderous-Software/roam/blob/a8d9ad6bc819d02195d047b103b96eb56d55063c/src/api/restApiServer.py), `restEnabled` in `config.yml`). It returns room summaries and entity lists, not the full per-tile save data, and requires a running game. Live export from it is a possible later feature, not part of this design.
- **No reproduction of in-game seeds.** Roam has none (see above). roam-usd does not patch Roam to add one; a per-world seed has been proposed upstream as [roam#600](https://github.com/Preponderous-Software/roam/issues/600), and `generate` will use it once a Roam version provides it. Even then, a seed reproduces a world only given the same room generation order.
- **No Roam code or art in this repository.** See the license boundary in [ARCHITECTURE.md](./ARCHITECTURE.md#license-boundary).
- **No photorealism.** Prototypes are simple shapes with flat colours. Creatures are flat sprite cards textured with Roam's own sprites, read from the user's Roam checkout at export time; textures for the other tiles are a later opt-in extra.

## Prior art

| Project | What it does | Relation to roam-usd |
| --- | --- | --- |
| [Mineways](https://github.com/erich666/Mineways) ([homepage](https://erich.realtimerendering.com/minecraft/public/mineways/)) | Exports Minecraft Java worlds to OBJ/VRML/STL for rendering and 3D printing, and (per its homepage, version 13.00) to USD with physically based materials. | The closest analogue: a block/tile game's saved world → a DCC-ready scene. roam-usd is far smaller in scope (2D grid, one tile type set) but follows the same "read the save, never the running game" model. |
| [Tiled2Unity](https://github.com/Seanba/Tiled2Unity) | Exports [Tiled](https://github.com/mapeditor/tiled) TMX maps into Unity prefabs. | The classic 2D tile map → 3D engine exporter; shows the value of keeping the converter outside both the editor and the engine. |
| [YATI](https://github.com/Kiamo2/YATI) | Godot 4 add-on that imports Tiled `.tmx` / `.tmj` maps. | Same idea, importer side: tile layers become engine-native nodes. |
| [OpenUSD `UsdGeomPointInstancer`](https://openusd.org/release/api/class_usd_geom_point_instancer.html) | USD's schema for massively instanced geometry: `prototypes`, `protoIndices`, `positions`, `orientations`, `scales`. | The core building block for tiles. |
| [Blender 4.1 USD import notes](https://developer.blender.org/docs/release_notes/4.1/pipeline_assets_io/) | Blender's built-in USD importer gained point-instancing import in 4.1. | Sets the minimum Blender version roam-usd targets. |

No existing tool that exports Roam worlds (or saves of any `py_env_lib`-based game) to a 3D format was found.
