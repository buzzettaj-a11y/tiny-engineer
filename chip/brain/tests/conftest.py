import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeRobot  # noqa: E402


@pytest.fixture
def robot():
    r = FakeRobot().start()
    yield r
    r.stop()


@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    caplog.set_level(logging.INFO, logger="chip")
