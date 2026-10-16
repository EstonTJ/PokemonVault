"""Startup must fail fast and readably when the database can't be reached (never hang, never leak the password)."""
import logging
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def unreachable_db(monkeypatch):
    from app.config import settings

    # Port 1 on localhost: connection refused immediately.
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://pv_user:s3cret-pass@127.0.0.1:1/pokevault")
    monkeypatch.setattr(settings, "db_connect_timeout_s", 3)
    return settings


def test_check_database_reports_reason_without_password(unreachable_db):
    from app.main import StartupError, check_database

    t = time.time()
    with pytest.raises(StartupError) as exc:
        check_database()
    assert time.time() - t < 10
    msg = str(exc.value)
    assert "Can't connect to the postgresql database" in msg and "127.0.0.1:1" in msg
    assert "s3cret-pass" not in msg and "***" in msg
    assert "DATABASE_URL" in msg


def test_app_startup_fails_fast_with_clear_log(unreachable_db, caplog):
    from app.main import app

    caplog.set_level(logging.INFO, logger="pokevault.startup")
    with pytest.raises(Exception):
        with TestClient(app):
            pass
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Connecting to the database" in text
    assert "STARTUP FAILED: Can't connect to the postgresql database" in text
    assert "s3cret-pass" not in text


def test_redacted_url_hides_password():
    from app.config import Settings

    s = Settings(database_url="postgresql+psycopg://neondb_owner:abc123@ep-x.neon.tech/neondb?sslmode=require")
    assert "abc123" not in s.database_url_redacted and "ep-x.neon.tech" in s.database_url_redacted


def test_postgres_connections_get_timeouts():
    from app.db import connect_args_for

    assert connect_args_for("postgresql+psycopg://x/y")["connect_timeout"] > 0
    assert "lock_timeout" in connect_args_for("postgresql+psycopg://x/y", for_migrations=True)["options"]
    assert "connect_timeout" not in connect_args_for("sqlite:///x.db")


def test_healthy_startup_logs_each_step(fresh_db, caplog):
    from app.main import app

    caplog.set_level(logging.INFO, logger="pokevault.startup")
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    text = "\n".join(r.getMessage() for r in caplog.records)
    for step in ("Starting PokéVault", "Database reachable", "Database schema up to date", "PokéVault is ready"):
        assert step in text, step
