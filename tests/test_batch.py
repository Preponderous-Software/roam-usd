"""M7: --seeds batches and the index.usda contact sheet."""

import itertools

import pytest
from pxr import Usd, UsdGeom

from roam_usd.cli import main
from roam_usd.generate import generate_batch, generate_seed, parse_seeds, seed_prim_name
from roam_usd.validate import validation_errors
from test_generate import write_fake_roam


@pytest.mark.parametrize(
    "text, seeds",
    [
        ("1..4", [1, 2, 3, 4]),
        ("1,5,9", [1, 5, 9]),
        ("1..3,7", [1, 2, 3, 7]),
        ("3", [3]),
        ("2,2,1..3", [2, 1, 3]),
        ("-2..1", [-2, -1, 0, 1]),
        (" 4 , 6 ", [4, 6]),
    ],
)
def test_parse_seeds(text, seeds):
    assert parse_seeds(text) == seeds


@pytest.mark.parametrize("text", ["", "1,,2", "4..1", "a", "1..b", "1-3", "0..20000"])
def test_parse_seeds_rejects(text):
    with pytest.raises(ValueError):
        parse_seeds(text)


def test_seed_prim_name():
    assert seed_prim_name(3) == "Seed_3" and seed_prim_name(-3) == "Seed_n3"


def test_seed_and_seeds_are_exclusive(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["generate", "--roam", str(tmp_path), "--seed", "1", "--seeds", "1..2"])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_bad_seeds_is_a_clean_error(tmp_path, capsys):
    roam = write_fake_roam(tmp_path / "fake")
    assert main(["generate", "--roam", str(roam), "--seeds", "5..1", "-o", str(tmp_path)]) == 1
    assert "--seeds" in capsys.readouterr().err


def seed_ranges(index_path):
    stage = Usd.Stage.Open(str(index_path))
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    return stage, {
        prim.GetCustomDataByKey("roam:seed"): cache.ComputeWorldBound(prim).ComputeAlignedRange()
        for prim in stage.GetPrimAtPath("/Index").GetChildren()
    }


def assert_no_overlap(ranges):
    assert len(ranges) >= 2
    for (a, ra), (b, rb) in itertools.combinations(ranges.items(), 2):
        assert not ra.IsEmpty() and not rb.IsEmpty()
        overlap_x = ra.GetMin()[0] < rb.GetMax()[0] and rb.GetMin()[0] < ra.GetMax()[0]
        overlap_y = ra.GetMin()[1] < rb.GetMax()[1] and rb.GetMin()[1] < ra.GetMax()[1]
        assert not (overlap_x and overlap_y), f"seed {a} overlaps seed {b}"


def check_batch(out, seeds):
    for seed in seeds:
        assert (out / f"seed_{seed}.usda").is_file()
    index = out / "index.usda"
    stage, ranges = seed_ranges(index)
    assert sorted(ranges) == sorted(seeds)
    assert_no_overlap(ranges)
    assert stage.GetDefaultPrim().GetPath() == "/Index"
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert list(stage.GetRootLayer().customLayerData["roam:seeds"]) == seeds
    assert validation_errors(str(index)) == []
    # the index references each seed's stage relatively, so the folder can move
    assert "@./seed_1.usda@" in index.read_text()


def test_batch_with_stand_in(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    out = tmp_path / "out"
    stages, index = generate_batch(roam, [1, 2, 3, 4], out, radius=1)
    assert index == out / "index.usda" and len(stages) == 4
    check_batch(out, [1, 2, 3, 4])
    # each stage equals a single --seed run with the same flags
    for seed in (1, 4):
        single = generate_seed(roam, seed, tmp_path / f"single{seed}", radius=1)
        assert single.read_bytes() == stages[seed].read_bytes()


def test_batch_index_layout_is_a_square_grid(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    _, index = generate_batch(roam, list(range(1, 6)), tmp_path / "out", radius=0)
    stage, ranges = seed_ranges(index)
    assert_no_overlap(ranges)
    rows = {round(r.GetMin()[1], 6) for r in ranges.values()}
    columns = {round(r.GetMin()[0], 6) for r in ranges.values()}
    assert len(rows) == 2 and len(columns) == 3  # ceil(sqrt(5)) = 3 columns


def test_batch_binary(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    out = tmp_path / "out"
    assert (
        main(
            ["generate", "--roam", str(roam), "--seeds", "1,2", "--radius", "0", "--binary"]
            + ["-o", str(out)]
        )
        == 0
    )
    index = out / "index.usdc"
    assert index.read_bytes()[:8] == b"PXR-USDC"
    stage = Usd.Stage.Open(str(index))
    assert stage.GetPrimAtPath("/Index/Seed_2/Surface/Room_0_0")


def test_batch_index_is_deterministic(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    _, a = generate_batch(roam, [1, 2], tmp_path / "a", radius=0)
    _, b = generate_batch(roam, [1, 2], tmp_path / "b", radius=0)
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.roam
def test_roam_batch_contact_sheet(tmp_path, roam_checkout, roam_python):
    out = tmp_path / "out"
    args = ["generate", "--roam", str(roam_checkout), "--seeds", "1..4", "--radius", "1"]
    if roam_python:
        args += ["--roam-python", roam_python]
    assert main([*args, "-o", str(out)]) == 0
    check_batch(out, [1, 2, 3, 4])
    single = generate_seed(roam_checkout, 3, tmp_path / "single", radius=1, roam_python=roam_python)
    assert single.read_bytes() == (out / "seed_3.usda").read_bytes()


def test_cli_negative_seeds_with_equals(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    out = tmp_path / "out"
    args = ["generate", "--roam", str(roam), "--seeds=-2..0,5", "--radius", "0", "-o", str(out)]
    assert main(args) == 0
    stage = Usd.Stage.Open(str(out / "index.usda"))
    assert list(stage.GetRootLayer().customLayerData["roam:seeds"]) == [-2, -1, 0, 5]
    assert stage.GetPrimAtPath("/Index/Seed_n2").GetCustomDataByKey("roam:seed") == -2
    assert (out / "seed_-2.usda").is_file()


def test_seeds_help_explains_negative_lists(capsys):
    with pytest.raises(SystemExit):
        main(["generate", "--help"])
    assert "--seeds=-2..1" in capsys.readouterr().out


def test_seeds_beyond_32_bits(tmp_path):
    roam = write_fake_roam(tmp_path / "fake")
    big = 2**31
    stages, index = generate_batch(roam, [big, -big - 1], tmp_path / "out", radius=0)
    stage = Usd.Stage.Open(str(index))
    assert list(stage.GetRootLayer().customLayerData["roam:seeds"]) == [big, -big - 1]
    assert stage.GetPrimAtPath(f"/Index/Seed_{big}").GetCustomDataByKey("roam:seed") == big
    seeded = Usd.Stage.Open(str(stages[big]))
    assert seeded.GetRootLayer().customLayerData["roam:seed"] == big
