"""Clearing the vault and removing selected Pokémon."""
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
def scanned(client, tmp_path):
    """A processed scan with every ready item confirmed into the vault."""
    from app.jobs.worker import run_one

    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    scan_id = client.post("/api/scans", params={"filename": "rec.mp4"}, content=video.read_bytes(),
                          headers={**H, "Content-Type": "video/mp4"}).json()["scan"]["id"]
    assert run_one("t")
    confirmed = client.post(f"/api/scans/{scan_id}/confirm-ready", headers=H).json()["confirmed"]
    assert confirmed >= 10
    return scan_id, confirmed


def _statuses(client, scan_id):
    gs = client.get(f"/api/scans/{scan_id}").json()["groups"]
    return {s: sum(1 for g in gs if g["status"] == s) for s in ("ready", "needs_review", "confirmed", "rejected")}


def test_clear_requires_exact_phrase(client, scanned):
    for wrong in ({}, {"confirm": "delete"}, {"confirm": "DELETE ALL"}, {"confirm": " DELETE"}):
        assert client.post("/api/pokemon/clear", json=wrong, headers=H).status_code == 400
    assert len(client.get("/api/pokemon").json()) == scanned[1]


def test_clear_empties_vault_but_keeps_scans_ready_to_rebuild(client, scanned):
    scan_id, confirmed = scanned
    pid = client.get("/api/pokemon").json()[0]["id"]
    assert client.patch(f"/api/pokemon/{pid}", json={"notes": "keep?"}, headers=H).status_code == 200

    r = client.post("/api/pokemon/clear", json={"confirm": "DELETE"}, headers=H)
    assert r.status_code == 200 and r.json()["removed"] == confirmed
    assert client.get("/api/pokemon").json() == []
    assert client.get(f"/api/pokemon/{pid}").status_code == 404
    st = _statuses(client, scan_id)
    assert st["confirmed"] == 0 and st["ready"] >= confirmed  # previously confirmed items are back, still confident
    assert client.get(f"/api/scans/{scan_id}").json()["status"] == "review"

    from app import db as dbmod
    from app.models import Correction, PokemonEvidence

    with dbmod.SessionLocal() as db:
        assert db.query(PokemonEvidence).count() == 0
        assert db.query(Correction).filter(Correction.target == "pokemon").count() == 0

    # Rebuilding gives the same vault back.
    assert client.post(f"/api/scans/{scan_id}/confirm-ready", headers=H).json()["confirmed"] >= confirmed


def test_remove_selected_only_touches_those(client, scanned):
    scan_id, confirmed = scanned
    pokes = client.get("/api/pokemon").json()
    chosen = [p["id"] for p in pokes[:3]]
    r = client.post("/api/pokemon/bulk-delete", json={"ids": chosen + ["no-such-id"]}, headers=H)
    assert r.status_code == 200 and r.json() == {"removed": 3, "not_found": 1}
    remaining = {p["id"] for p in client.get("/api/pokemon").json()}
    assert len(remaining) == confirmed - 3 and not (remaining & set(chosen))
    st = _statuses(client, scan_id)
    assert st["confirmed"] == confirmed - 3


def test_removed_pokemon_disappear_from_identity_suggestions(client, scanned, tmp_path):
    """A pending item that suggested 'same as Pokémon X' must not keep pointing at X after X is removed."""
    from app.jobs.worker import run_one
    from tests.synthetic import DEFAULT_GRID

    scan_id, _ = scanned
    second = tmp_path / "again.mp4"
    write_video(second, mons=list(reversed(DEFAULT_GRID[:9])), include_detail=False)
    s2 = client.post("/api/scans", params={"filename": "again.mp4"}, content=second.read_bytes(),
                     headers={**H, "Content-Type": "video/mp4"}).json()["scan"]["id"]
    assert run_one("t")
    with_cands = [g for g in client.get(f"/api/scans/{s2}").json()["groups"] if g["candidates"]]
    assert with_cands
    target = with_cands[0]["candidates"][0]["pokemon_id"]
    assert client.post("/api/pokemon/bulk-delete", json={"ids": [target]}, headers=H).status_code == 200
    for g in client.get(f"/api/scans/{s2}").json()["groups"]:
        assert all(c["pokemon_id"] != target for c in g["candidates"])


@pytest.mark.parametrize("body", [{}, {"ids": []}, {"ids": "abc"}, {"ids": [1, 2]}])
def test_bulk_delete_validates_input(client, body):
    assert client.post("/api/pokemon/bulk-delete", json=body, headers=H).status_code == 400


def test_requires_owner_and_csrf(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/pokemon/clear", json={"confirm": "DELETE"}, headers=H).status_code == 401
        c.post("/api/auth/login", json={"password": "test-password"})
        assert c.post("/api/pokemon/clear", json={"confirm": "DELETE"}).status_code == 403
        assert c.post("/api/pokemon/bulk-delete", json={"ids": ["x"]}).status_code == 403
