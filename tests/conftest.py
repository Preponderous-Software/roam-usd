from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def basic_save() -> Path:
    return FIXTURES / "saves" / "basic"


@pytest.fixture
def malformed_save() -> Path:
    return FIXTURES / "saves" / "malformed"
