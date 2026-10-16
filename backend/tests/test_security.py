"""Auth hardening: logout CSRF, throttles that survive IP spoofing, proxy trust."""
import itertools
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

H = {"X-PokeVault": "1"}


@pytest.fixture()
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def spoofed_ips(monkeypatch):
    """Every request appears to come from a new address, as with a spoofed X-Forwarded-For."""
    from app import auth

    counter = itertools.count()
    monkeypatch.setattr(auth, "_client", lambda request: f"203.0.113.{next(counter) % 250}")


def test_logout_requires_csrf_header(client):
    assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 200
    assert client.post("/api/auth/logout").status_code == 403           # cross-site style request
    assert client.get("/api/auth/me").json()["authenticated"] is True   # still signed in
    assert client.post("/api/auth/logout", headers=H).status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_per_client_login_limit(client):
    for _ in range(8):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 429


def test_login_limit_holds_when_ip_is_spoofed(client, spoofed_ips):
    from app import auth

    for _ in range(auth.MAX_GLOBAL_FAILURES):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    # A fresh "address" no longer helps: the global cap applies to everyone, even the right password.
    assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 429


def test_successful_logins_do_not_count_against_the_limit(client):
    for _ in range(20):
        assert client.post("/api/auth/login", json={"password": "test-password"}).status_code == 200


def test_upload_key_guessing_is_capped_globally(client, spoofed_ips):
    from app import auth

    for _ in range(auth.MAX_KEY_FAILURES):
        r = client.post("/api/scans", params={"filename": "x.mp4"}, content=b"x",
                        headers={"Authorization": "Bearer pvu_guess"})
        assert r.status_code == 401
    r = client.post("/api/scans", params={"filename": "x.mp4"}, content=b"x", headers={"Authorization": "Bearer pvu_guess"})
    assert r.status_code == 429


def test_start_script_does_not_trust_every_proxy():
    start = (Path(__file__).resolve().parents[2] / "start.sh").read_text()
    assert 'forwarded-allow-ips="*"' not in start
    assert "FORWARDED_ALLOW_IPS:-127.0.0.1" in start
