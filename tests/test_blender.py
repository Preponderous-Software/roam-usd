"""M8: headless Blender import smoke test (opt-in: ``pytest -m blender``).

Blender's ``bpy`` module runs in its own interpreter (ROAM_USD_BLENDER_PYTHON, e.g. a
Python 3.11 environment with ``pip install bpy==4.2.0``), never in the same process
as usd-core's ``pxr``. The test exports stages with roam-usd, then has Blender import
them with ``scripts/blender_check.py``.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from roam_usd.save import read_bundle_world, read_save_dir
from roam_usd.usd import ExportOptions, export_world
from test_individuals import png_bytes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "blender_check.py"
pytestmark = pytest.mark.blender


@pytest.fixture
def blender_python():
    value = os.environ.get("ROAM_USD_BLENDER_PYTHON")
    if not value:
        pytest.skip("set ROAM_USD_BLENDER_PYTHON to an interpreter that has Blender's bpy")
    return value


def blender_summary(python, stage, *extra):
    result = subprocess.run(
        [python, str(SCRIPT), str(stage), *extra],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    lines = [x for x in result.stdout.splitlines() if x.startswith("ROAM_USD_BLENDER_SUMMARY ")]
    assert result.returncode == 0 and lines, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(lines[-1].split(" ", 1)[1])


def test_blender_imports_fixture_world(tmp_path, basic_save, blender_python):
    roam = tmp_path / "checkout"
    (roam / "assets" / "images").mkdir(parents=True)
    for name in ("chicken", "bear", "npc", "rabbit", "snake"):
        (roam / "assets" / "images" / f"{name}.png").write_bytes(png_bytes())
    stage = export_world(
        read_save_dir(basic_save), tmp_path / "w.usda", options=ExportOptions(roam_checkout=roam)
    )
    summary = blender_summary(blender_python, stage, "--render", str(tmp_path / "w.png"))
    assert summary["imported"] is True
    major, minor = (int(v) for v in summary["blender"].split(".")[:2])
    assert (major, minor) >= (4, 1)
    assert summary["levels"] == {"Surface": 0.0, "Cave_1": -4.0}
    assert summary["grounds"] == 4 and summary["instancers"] == 4
    assert summary["creatures"] == [
        "Bear_6_5_1",
        "Chicken_5_5_1",
        "Npc_12_12_1",
        "Rabbit_16_1_1",
        "Snake_14_13_1",
    ]
    assert summary["structures"] == ["Chest_9_9", "MatureCrop_11_9", "YoungCrop_10_9"]
    assert summary["player"] is True
    assert len(summary["images"]) == 5  # every sprite card's texture was loaded
    assert (tmp_path / "w.png").stat().st_size > 10_000


def test_blender_imports_bundle_world(tmp_path, blender_python):
    from conftest import FIXTURES

    world = read_bundle_world(FIXTURES / "bundles" / "roam-saves-two-worlds.json", "caves")
    stage = export_world(world, tmp_path / "caves.usdc", binary=True)
    summary = blender_summary(blender_python, stage)
    assert summary["levels"] == {"Surface": 0.0, "Cave_1": -4.0}
