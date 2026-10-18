"""M8: browser save bundles (`roam-saves-<date>.json`)."""

import json

import pytest
from pxr import Usd

from roam_usd.cli import main
from roam_usd.save import SaveFormatError, read_bundle, read_bundle_world, read_save


@pytest.fixture
def bundle(request):
    from conftest import FIXTURES

    return FIXTURES / "bundles" / "roam-saves-two-worlds.json"


def write_bundle(path, files, top=None):
    data = {"format": "roam-saves", "version": 1, "game": "roam", "files": files}
    data.update(top or {})
    path.write_text(json.dumps(data))
    return path


ROOM = json.dumps(
    {
        "backgroundColor": "(1, 2, 3)",
        "x": 0,
        "y": 0,
        "grid": {"columns": 2, "rows": 2, "locations": []},
    }
)


def test_lists_worlds(bundle):
    assert list(read_bundle(bundle)) == ["caves", "save_1"]


def test_reads_each_world_in_memory(bundle, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # nothing may be written next to the caller
    one = read_bundle_world(bundle, "save_1")
    assert {(r.x, r.y, r.z) for r in one.rooms()} == {(0, 0, 0), (1, 0, 0)}
    assert one.tick == 42 and one.player.location_id == "b-1-2-2"
    assert one.find_room(0, 0, 0).room_type == "grassland"
    assert one.find_room(1, 0, 0).room_type == "unknown"
    caves = read_bundle_world(bundle, "caves")
    assert [lvl.name for lvl in caves.levels] == ["Surface", "Cave_1"]
    assert caves.player is None
    assert list(tmp_path.iterdir()) == []


def test_world_required_when_several(bundle):
    with pytest.raises(SaveFormatError) as exc:
        read_bundle_world(bundle)
    assert "--world" in str(exc.value) and "caves, save_1" in str(exc.value)


def test_unknown_world_lists_names(bundle):
    with pytest.raises(SaveFormatError, match="caves, save_1"):
        read_bundle_world(bundle, "nope")


def test_single_world_needs_no_name(tmp_path):
    path = write_bundle(tmp_path / "b.json", {"/saves/only/rooms/room_0_0.json": ROOM})
    assert read_bundle_world(path).source.endswith("#only")


def test_base64_room_file(tmp_path):
    import base64

    encoded = base64.b64encode(ROOM.encode()).decode()
    path = write_bundle(tmp_path / "b.json", {"/saves/w/rooms/room_0_0.json": {"base64": encoded}})
    assert read_bundle_world(path).find_room(0, 0, 0) is not None


@pytest.mark.parametrize(
    "top, files, message",
    [
        ({"format": "other"}, {}, "not a Roam save bundle"),
        ({"version": 2}, {"/saves/w/rooms/room_0_0.json": ROOM}, "unsupported bundle version"),
        ({"files": []}, None, "'files'"),
        ({}, {}, "no worlds"),
        ({}, {"/elsewhere/w/x.json": ROOM}, "not under /saves/"),
        ({}, {"/saves/w": ROOM}, "names no world file"),
        ({}, {"/saves/w/tick.json": "{}"}, "no rooms/"),
        ({}, {"/saves/w/rooms/room_0_0.json": 5}, "content must be"),
        ({}, {"/saves/w/rooms/room_0_0.json": {"base64": "!!"}}, "invalid base64"),
        ({}, {"/saves/w/rooms/room_0_0.json": "{"}, "room_0_0.json: not valid JSON"),
    ],
)
def test_invalid_bundles(tmp_path, top, files, message):
    path = tmp_path / "b.json"
    write_bundle(path, files if files is not None else {}, top)
    with pytest.raises(SaveFormatError, match=message):
        read_bundle_world(path)


def test_not_json(tmp_path):
    path = tmp_path / "b.json"
    path.write_text("not json")
    with pytest.raises(SaveFormatError, match="not valid JSON"):
        read_bundle(path)


def test_read_save_dispatch(tmp_path, bundle, basic_save):
    assert read_save(basic_save).find_room(0, 0, -1) is not None
    assert read_save(bundle, "caves").find_room(0, 0, -1) is not None
    with pytest.raises(SaveFormatError, match="--world only applies"):
        read_save(basic_save, "save_1")
    with pytest.raises(SaveFormatError, match="no such"):
        read_save(tmp_path / "missing")


def test_cli_info_lists_both_worlds(bundle, capsys):
    assert main(["info", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert "Worlds: 2" in out and "caves: 2 rooms" in out and "save_1: 2 rooms" in out


def test_cli_info_one_world(bundle, capsys):
    assert main(["info", str(bundle), "--world", "save_1"]) == 0
    out = capsys.readouterr().out
    assert "Tick: 42" in out and "Player: room (0, 0, 0), tile (2, 2)" in out


@pytest.mark.parametrize("world, rooms", [("save_1", 2), ("caves", 2)])
def test_cli_export_each_world(bundle, tmp_path, world, rooms):
    out = tmp_path / f"{world}.usda"
    assert main(["export", str(bundle), "--world", world, "-o", str(out)]) == 0
    stage = Usd.Stage.Open(str(out))
    assert len([p for p in stage.Traverse() if p.GetName().startswith("Room_")]) == rooms
    data = stage.GetRootLayer().customLayerData
    assert data["roam:savePath"].endswith(f"#{world}")


def test_cli_export_needs_world(bundle, tmp_path, capsys):
    assert main(["export", str(bundle), "-o", str(tmp_path / "w.usda")]) == 1
    assert "choose one with --world" in capsys.readouterr().err
