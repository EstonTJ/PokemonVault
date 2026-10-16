"""Refuse to silently lose data on hosts whose disk is reset on redeploy (e.g. Replit deployments)."""
import pytest
from fastapi.testclient import TestClient

from tests.synthetic import write_video

H = {"X-PokeVault": "1"}


@pytest.fixture()
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"password": "test-password"}).status_code == 200
        yield c


@pytest.fixture()
def replit_with_sqlite(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "on_replit_deployment", True)
    assert settings.database_kind == "sqlite"
    return settings


def test_local_sqlite_is_fine(client):
    assert client.get("/api/health").json() == {"ok": True, "storage_persistent": True}
    st = client.get("/api/settings").json()["storage"]
    assert st["persistent"] and st["accepting_uploads"] and not st["ephemeral_host"]


def test_replit_deployment_with_sqlite_refuses_uploads(client, replit_with_sqlite, tmp_path):
    assert client.get("/api/health").json()["storage_persistent"] is False
    st = client.get("/api/settings").json()["storage"]
    assert st == {**st, "persistent": False, "accepting_uploads": False, "database": "sqlite", "ephemeral_host": True}

    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    r = client.post("/api/scans", params={"filename": "rec.mp4"}, content=video.read_bytes(),
                    headers={**H, "Content-Type": "video/mp4"})
    assert r.status_code == 503 and "erased on the next redeploy" in r.json()["detail"]
    r = client.post("/api/uploads", json={"filename": "rec.mp4", "size": 1000}, headers=H)
    assert r.status_code == 503
    assert client.get("/api/scans").json() == []


def test_explicit_override_allows_uploads_but_still_warns(client, replit_with_sqlite, monkeypatch):
    monkeypatch.setattr(replit_with_sqlite, "allow_ephemeral_storage", True)
    assert client.get("/api/health").json()["storage_persistent"] is False
    assert client.post("/api/uploads", json={"filename": "rec.mp4", "size": 1000}, headers=H).status_code == 200


def test_postgres_on_replit_counts_as_persistent(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("REPLIT_DEPLOYMENT", "1")
    monkeypatch.delenv("POKEVAULT_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example/neondb?sslmode=require")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@db.example/neondb?sslmode=require"
    assert s.database_kind == "postgresql" and s.storage_persistent and s.storage_writable
    assert s.storage_status()["files_survive_redeploy"] is False  # evidence images are still on local disk


def test_database_url_precedence(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://replit/db")
    monkeypatch.setenv("POKEVAULT_DATABASE_URL", "postgresql://explicit/db")
    assert Settings().database_url == "postgresql+psycopg://explicit/db"
    monkeypatch.delenv("POKEVAULT_DATABASE_URL")
    monkeypatch.delenv("DATABASE_URL")
    assert Settings().database_url.startswith("sqlite:///")
