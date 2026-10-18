"""``roam-usd generate``: drive Roam's own world generator from a seed, then export.

The generator runs in a subprocess (``roam_usd/_driver.py``) with the user's Roam
checkout as working directory, writes a genuine Roam save plus
``roam-usd-manifest.json`` into a temporary directory, and that save goes through the
same reader and writer as ``roam-usd export`` (ARCHITECTURE.md § Seed driver).
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from roam_usd._driver import RESULT_PREFIX
from roam_usd.model import UNKNOWN_ROOM_TYPE, Level, World
from roam_usd.save import SaveFormatError, read_save_dir
from roam_usd.usd import ExportOptions, export_world, output_format

DRIVER = Path(__file__).with_name("_driver.py")
MANIFEST = "roam-usd-manifest.json"
MAX_CAVE_DEPTH = 3


class GenerateError(RuntimeError):
    """The Roam generator could not be run or failed."""


def check_checkout(roam: str | Path) -> Path:
    """Resolve ``roam`` and check it looks like a Roam checkout."""
    root = Path(roam).expanduser().resolve()
    if not (root / "src" / "world" / "map.py").is_file():
        raise GenerateError(f"--roam {roam}: not a Roam checkout (no src/world/map.py)")
    return root


def run_driver(
    roam: str | Path,
    seed: int,
    save_dir: str | Path,
    radius: int = 2,
    cave_depth: int = MAX_CAVE_DEPTH,
    home: bool = True,
    roam_python: str | None = None,
) -> Path:
    """Run the seed driver once; returns ``save_dir``, now holding a Roam save + manifest."""
    if radius < 0:
        raise GenerateError("--radius must be >= 0")
    if not 0 <= cave_depth <= MAX_CAVE_DEPTH:
        raise GenerateError(f"--cave-depth must be between 0 and {MAX_CAVE_DEPTH}")
    checkout = check_checkout(roam)
    save = Path(save_dir)
    save.mkdir(parents=True, exist_ok=True)
    command = [
        interpreter_path(roam_python),
        "-I",  # no script directory, user site or PYTHON* variables on sys.path
        "-B",  # never write __pycache__ into the user's checkout
        str(DRIVER),
        "--checkout",
        str(checkout),
        "--out",
        str(save.resolve()),
        "--seed",
        str(seed),
        "--radius",
        str(radius),
        "--cave-depth",
        str(cave_depth),
    ]
    if not home:
        command.append("--no-home")
    driver_result(_run(command, checkout).stdout)
    return save


def list_roam_classes(roam: str | Path, roam_python: str | None = None) -> list[str]:
    """Entity classes Roam's loader knows at ``roam`` (read at run time by the driver)."""
    checkout = check_checkout(roam)
    command = [interpreter_path(roam_python), "-I", "-B", str(DRIVER), "--checkout"]
    result = _run([*command, str(checkout), "--list-classes"], checkout)
    return driver_result(result.stdout)


def driver_result(stdout: str) -> Any:
    """The driver's JSON result: the last stdout line with the result prefix. Anything
    else on stdout (e.g. a banner printed by one of Roam's dependencies) is ignored."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX) :])
    raise GenerateError("Roam's generator gave no result:\n" + stdout[-2000:])


def interpreter_path(roam_python: str | None) -> str:
    """The interpreter to run the driver with. A path (anything with a separator) is made
    absolute against the caller's directory, since the driver runs with the checkout as
    its working directory; symlinks are kept (a venv's python is one). A bare name is
    left for PATH lookup."""
    if not roam_python:
        return sys.executable
    seps = [os.sep] + ([os.altsep] if os.altsep else [])
    if any(sep in roam_python for sep in seps):
        return os.path.abspath(os.path.expanduser(roam_python))
    return roam_python


def _run(command: list[str], checkout: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Keep Roam's structured logging quiet and off disk.
    env.update({"LOG_LEVEL": "ERROR", "LOG_FILE": "", "PYGAME_HIDE_SUPPORT_PROMPT": "1"})
    try:
        result = subprocess.run(
            command, cwd=checkout, env=env, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise GenerateError(f"could not run {command[0]}: {exc}") from None
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        hint = ""
        if "ModuleNotFoundError" in result.stderr:
            hint = (
                "\nhint: install Roam's requirements (pip install -r <roam>/requirements.txt) "
                "into the interpreter given with --roam-python"
            )
        raise GenerateError(f"Roam's generator failed:\n{tail}{hint}")
    return result


def read_manifest(save_dir: str | Path) -> dict[str, Any]:
    path = Path(save_dir) / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SaveFormatError(f"{path}: cannot read the manifest ({exc})") from None
    if not isinstance(manifest, dict) or manifest.get("format") != "roam-usd-manifest":
        raise SaveFormatError(f"{path}: not a roam-usd manifest")
    return manifest


def apply_manifest(world: World, manifest: Mapping[str, Any]) -> World:
    """Fill each room's ``roomType`` from the manifest where the room file had none."""
    types = manifest.get("roomTypes", {})
    levels = []
    for level in world.levels:
        rooms = []
        for room in level.rooms:
            saved = types.get(f"{room.x},{room.y},{room.z}")
            if room.room_type == UNKNOWN_ROOM_TYPE and isinstance(saved, str) and saved:
                room = dataclasses.replace(room, room_type=saved)
            rooms.append(room)
        levels.append(Level(level.z, tuple(rooms)))
    return dataclasses.replace(world, levels=tuple(levels), manifest=dict(manifest))


def read_generated(save_dir: str | Path, source: str | None = None) -> World:
    """Read a save written by the driver, with room types and provenance from its manifest."""
    manifest = read_manifest(save_dir)
    world = read_save_dir(save_dir, source=source)
    return apply_manifest(world, manifest)


def layer_data(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """``customLayerData`` for a seeded stage. Deliberately excludes anything that varies
    between identical runs (paths, times, the Python micro version)."""
    return {
        "roam:source": "seed",
        "roam:seed": int(manifest["seed"]),
        "roam:seedMechanism": str(manifest.get("seedMechanism", "global-random")),
        "roam:commit": str(manifest.get("roamCommit", "unknown")),
        "roam:version": str(manifest.get("roamVersion", "unknown")),
        "roam:radius": int(manifest.get("radius", 0)),
        "roam:caveDepth": int(manifest.get("caveDepth", 0)),
        "roam:home": bool(manifest.get("home", True)),
    }


def stage_name(seed: int, binary: bool = False) -> str:
    return f"seed_{seed}.usdc" if binary else f"seed_{seed}.usda"


def generate_seed(
    roam: str | Path,
    seed: int,
    out_dir: str | Path,
    radius: int = 2,
    cave_depth: int = MAX_CAVE_DEPTH,
    home: bool = True,
    binary: bool = False,
    roam_python: str | None = None,
    keep_save: bool = False,
) -> Path:
    """Generate the world for ``seed`` and write ``<out_dir>/seed_<seed>.usda``."""
    out = Path(out_dir)
    stage_path = out / stage_name(seed, binary)
    output_format(stage_path, binary)
    checkout = check_checkout(roam)
    kept = out / f"seed_{seed}_save"
    if keep_save and kept.exists() and not (kept / MANIFEST).is_file():
        raise GenerateError(f"{kept} exists and was not written by roam-usd")
    with tempfile.TemporaryDirectory(prefix=f"roam-usd-seed-{seed}-") as tmp:
        save_dir = Path(tmp) / "save"
        run_driver(checkout, seed, save_dir, radius, cave_depth, home, roam_python)
        world = read_generated(save_dir, source=f"seed {seed}")
        options = ExportOptions(
            layer_data=layer_data(world.manifest or {}),
            roam_checkout=checkout,
            include_ids=False,
        )
        stage = export_world(world, stage_path, binary=binary, options=options)
        if keep_save:
            # Replace an earlier kept save only once this run has fully succeeded.
            if kept.exists():
                shutil.rmtree(kept)
            shutil.copytree(save_dir, kept)
        return stage


# --- batches and the contact sheet (M7) -------------------------------------------------

_RANGE = re.compile(r"^(-?\d+)\.\.(-?\d+)$")
_SINGLE = re.compile(r"^-?\d+$")
MAX_BATCH = 10_000


def parse_seeds(text: str) -> list[int]:
    """``"1..16"``, ``"1,5,9"`` or a mix (``"1..3,7"``) → seeds in order, without repeats."""
    seeds: list[int] = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            raise ValueError(f"empty seed in {text!r}")
        match = _RANGE.match(part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if end < start:
                raise ValueError(f"seed range {part!r} runs backwards")
            if end - start >= MAX_BATCH:
                raise ValueError(f"seed range {part!r} is larger than {MAX_BATCH} seeds")
            seeds.extend(range(start, end + 1))
        elif _SINGLE.match(part):
            seeds.append(int(part))
        else:
            raise ValueError(f"not a seed or seed range: {part!r} (use A..B or A,B,C)")
    unique = list(dict.fromkeys(seeds))
    if len(unique) > MAX_BATCH:
        raise ValueError(f"more than {MAX_BATCH} seeds")
    return unique


def seed_prim_name(seed: int) -> str:
    return f"Seed_n{-seed}" if seed < 0 else f"Seed_{seed}"


def index_name(binary: bool = False) -> str:
    return "index.usdc" if binary else "index.usda"


def _world_range(path: Path) -> Gf.Range3d:
    stage = Usd.Stage.Open(str(path))
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    return cache.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()


def write_index(stages: Mapping[int, Path], out_dir: str | Path, binary: bool = False) -> Path:
    """Write ``index.usda``: every seed stage referenced under ``/Index/Seed_<n>``, laid out
    in a square grid whose cells fit the largest world plus a margin, so none overlap."""
    if not stages:
        raise GenerateError("no seed stages to index")
    out = Path(out_dir)
    index_path = out / index_name(binary)
    output_format(index_path, binary)

    ranges = {seed: _world_range(path) for seed, path in stages.items()}
    width = max(r.GetSize()[0] for r in ranges.values())
    depth = max(r.GetSize()[1] for r in ranges.values())
    margin = max(4.0, 0.1 * max(width, depth))
    cell_x, cell_y = width + margin, depth + margin
    columns = math.ceil(math.sqrt(len(stages)))

    # Author on a layer anchored at the index's own path, so the relative references
    # resolve while composing (an anonymous layer cannot anchor them).
    index_path.parent.mkdir(parents=True, exist_ok=True)
    layer = Sdf.Layer.Find(str(index_path))
    if layer:
        layer.Clear()
    else:
        layer = Sdf.Layer.CreateNew(str(index_path))
    if not layer:
        raise GenerateError(f"{index_path}: cannot create the index")
    stage = Usd.Stage.Open(layer)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Index").GetPrim()
    stage.SetDefaultPrim(root)
    stage.GetRootLayer().customLayerData = {
        "roam:source": "seeds",
        "roam:seeds": Vt.IntArray(list(stages)),
        "roam:cellSize": Gf.Vec2d(cell_x, cell_y),
    }
    for i, (seed, path) in enumerate(stages.items()):
        row, column = divmod(i, columns)
        lo = ranges[seed].GetMin()
        # Put the world's bounding-box corner at its cell's corner (rows run towards -Y).
        offset = Gf.Vec3d(column * cell_x - lo[0], -row * cell_y - (lo[1] + depth), 0.0)
        xform = UsdGeom.Xform.Define(stage, root.GetPath().AppendChild(seed_prim_name(seed)))
        xform.AddTranslateOp().Set(offset)
        prim = xform.GetPrim()
        prim.GetReferences().AddReference("./" + Path(path).name)
        prim.SetCustomDataByKey("roam:seed", seed)
    if not layer.Save(force=True):
        raise GenerateError(f"{index_path}: could not write the index")
    return index_path


def generate_batch(
    roam: str | Path,
    seeds: list[int],
    out_dir: str | Path,
    radius: int = 2,
    cave_depth: int = MAX_CAVE_DEPTH,
    home: bool = True,
    binary: bool = False,
    roam_python: str | None = None,
    keep_save: bool = False,
    progress=None,
) -> tuple[dict[int, Path], Path]:
    """Generate one stage per seed (one driver process each, so worlds share no state),
    then the ``index`` contact sheet. Returns ({seed: stage}, index path)."""
    stages: dict[int, Path] = {}
    for seed in seeds:
        stages[seed] = generate_seed(
            roam, seed, out_dir, radius, cave_depth, home, binary, roam_python, keep_save
        )
        if progress is not None:
            progress(stages[seed])
    return stages, write_index(stages, out_dir, binary)
