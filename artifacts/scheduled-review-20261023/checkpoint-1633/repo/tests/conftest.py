import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bootstrap_raw():
    return json.loads((FIXTURES / "bootstrap-static.json").read_text(encoding="utf-8"))


@pytest.fixture
def fixtures_raw():
    return json.loads((FIXTURES / "fixtures.json").read_text(encoding="utf-8"))
