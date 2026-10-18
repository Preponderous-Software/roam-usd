"""``roam-usd generate``: drive Roam's own world generator from a seed, then export.

The generator runs in a subprocess (``roam_usd/_driver.py``) with the user's Roam
checkout as working directory, writes a genuine Roam save plus
``roam-usd-manifest.json`` into a temporary directory, and that save goes through the
same reader and writer as ``roam-usd export`` (ARCHITECTURE.md § Seed driver).
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
        roam_python or sys.executable,
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
    command = [roam_python or sys.executable, "-I", "-B", str(DRIVER), "--checkout"]
    result = _run([*command, str(checkout), "--list-classes"], checkout)
    return driver_result(result.stdout)


def driver_result(stdout: str) -> Any:
    """The driver's JSON result: the last stdout line with the result prefix. Anything
    else on stdout (e.g. a banner printed by one of Roam's dependencies) is ignored."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX) :])
    raise GenerateError("Roam's generator gave no result:\n" + stdout[-2000:])


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
    with tempfile.TemporaryDirectory(prefix=f"roam-usd-seed-{seed}-") as tmp:
        save_dir = out / f"seed_{seed}_save" if keep_save else Path(tmp) / "save"
        if keep_save and save_dir.exists():
            if not (save_dir / MANIFEST).is_file():
                raise GenerateError(f"{save_dir} exists and was not written by roam-usd")
            shutil.rmtree(save_dir)
        run_driver(checkout, seed, save_dir, radius, cave_depth, home, roam_python)
        world = read_generated(save_dir, source=f"seed {seed}")
        options = ExportOptions(
            layer_data=layer_data(world.manifest or {}),
            roam_checkout=checkout,
            include_ids=False,
        )
        return export_world(world, stage_path, binary=binary, options=options)
