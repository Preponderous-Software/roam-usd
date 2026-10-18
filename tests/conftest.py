from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite tests/golden/ from the current output instead of comparing",
    )


@pytest.fixture
def update_golden(request) -> bool:
    return request.config.getoption("--update-golden")


@pytest.fixture
def basic_save() -> Path:
    return FIXTURES / "saves" / "basic"


@pytest.fixture
def malformed_save() -> Path:
    return FIXTURES / "saves" / "malformed"


def check_golden(actual: Path, name: str, update: bool) -> None:
    """Byte-compare ``actual`` with ``tests/golden/<name>``; ``--update-golden`` rewrites it."""
    golden = GOLDEN / name
    data = actual.read_bytes()
    if update:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(data)
        return
    assert golden.exists(), f"missing {golden}; run pytest --update-golden to create it"
    assert data == golden.read_bytes(), (
        f"{actual} differs from {golden}; if the change is intended, run pytest --update-golden"
    )
