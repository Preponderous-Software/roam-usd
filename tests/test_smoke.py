import subprocess
import sys

import pytest

import roam_usd
from roam_usd.cli import main


def test_pxr_imports():
    from pxr import Sdf, Usd, UsdGeom  # noqa: F401

    stage = Usd.Stage.CreateInMemory()
    assert stage.DefinePrim("/World", "Xform")


def test_version_string():
    assert isinstance(roam_usd.__version__, str) and roam_usd.__version__


def test_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ("export", "generate", "info"):
        assert command in out


def test_console_script_help():
    result = subprocess.run(
        [sys.executable, "-m", "roam_usd", "--help"], capture_output=True, text=True, check=True
    )
    for command in ("export", "generate", "info"):
        assert command in result.stdout


def test_missing_subcommand_is_an_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


@pytest.mark.parametrize("command", ["export", "generate", "info"])
def test_stubs_exit_non_zero(command, capsys):
    assert main([command]) != 0
    assert "not yet implemented" in capsys.readouterr().err
