# Test fixtures

Every file under this directory was written by hand for roam-usd's tests. None of
them is Roam output, and none is copied or derived from Roam's source, schemas,
assets or real saves (see ARCHITECTURE.md § License boundary). Field names follow
the save layout documented in ARCHITECTURE.md § Save reader; IDs are readable
placeholders, not UUIDs.

- `saves/basic/` — a small world: surface rooms (one with `roomType`, one without
  `z` or `roomType`, one with negative coordinates and a 5×5 grid), a cave room,
  creatures, a chest, crops, an NPC, an unknown entity class, a `Player` record,
  `playerLocation.json` and `tick.json`. Grids are sparse: only the tiles a test
  needs are listed.
- `saves/malformed/` — a room file with no `grid`.
