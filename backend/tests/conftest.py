import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated data dir for the whole test session (set before app modules import settings).
_TMP = tempfile.mkdtemp(prefix="pokevault-test-")
os.environ["POKEVAULT_DATA_DIR"] = _TMP
os.environ["POKEVAULT_PASSWORD"] = "test-password"
os.environ["POKEVAULT_INPROCESS_WORKER"] = "0"
os.environ["POKEVAULT_FRONTEND_DIST"] = str(Path(_TMP) / "no-dist")
# The test client talks plain HTTP, so Secure cookies would never be sent back. Production keeps them on.
os.environ["POKEVAULT_SECURE_COOKIES"] = "0"
# Never let tests touch a real database configured in the environment (e.g. Replit PostgreSQL).
os.environ["POKEVAULT_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'session.db'}"


@pytest.fixture()
def fresh_db(tmp_path):
    from app import db as dbmod
    from app.migrate import upgrade

    url = f"sqlite:///{tmp_path / 'test.db'}"
    upgrade(url)
    dbmod.configure(url)
    yield dbmod


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    from app import auth

    auth._attempts.clear()
    auth._global_failures.clear()
    yield
