import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
# Point the database at a throwaway file *before* store is imported (it reads the path at import).
_tmp = Path(tempfile.mkdtemp())
os.environ["CARECIRCLE_DB"] = str(_tmp / "test.db")
os.environ["CARECIRCLE_OUTBOX"] = str(_tmp / "outbox.jsonl")
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "simulator"), str(ROOT / "scripts")]

import store  # noqa: E402
from seed_demo import seed  # noqa: E402


@pytest.fixture
def conn():
    """A freshly seeded demo household (Margaret), with the care clock back on real time afterwards."""
    seed()
    c = store.connect()
    yield c
    c.close()
    store.set_clock(None)
