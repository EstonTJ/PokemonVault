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


@pytest.fixture(scope="session")
def _postgres_url():
    """With POKEVAULT_TEST_POSTGRES=1, run every DB test on a real throwaway PostgreSQL (pip install pgserver)."""
    if os.environ.get("POKEVAULT_TEST_POSTGRES") != "1":
        yield None
        return
    import pgserver

    srv = pgserver.get_server(Path(_TMP) / "pg", cleanup_mode="stop")
    yield srv.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    srv.cleanup()


@pytest.fixture()
def fresh_db(tmp_path, _postgres_url):
    from app import db as dbmod
    from app.migrate import upgrade

    if _postgres_url:
        import sqlalchemy as sa

        eng = sa.create_engine(_postgres_url)
        with eng.begin() as c:  # a clean schema per test, built by the real migrations
            c.execute(sa.text("DROP SCHEMA public CASCADE"))
            c.execute(sa.text("CREATE SCHEMA public"))
        eng.dispose()
        url = _postgres_url
    else:
        url = f"sqlite:///{tmp_path / 'test.db'}"
    upgrade(url)
    dbmod.configure(url)
    yield dbmod
    dbmod.engine.dispose()


@pytest.fixture(autouse=True)
def _reset_auth_limits():
    from app import auth

    auth._attempts.clear()
    auth._global_failures.clear()
    yield
