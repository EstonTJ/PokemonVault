"""Upload-only key (iOS Shortcut) and opt-in auto-confirm."""
import pytest
from fastapi.testclient import TestClient

from tests.synthetic import write_video

H = {"X-PokeVault": "1"}


@pytest.fixture()
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "test-password"})
        yield c


@pytest.fixture()
def anon(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_upload_key_can_only_upload(client, anon, tmp_path):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    body = video.read_bytes()

    # No key yet: bearer upload rejected.
    r = anon.post("/api/scans", params={"filename": "rec.mp4"}, content=body, headers={"Authorization": "Bearer pvu_nope"})
    assert r.status_code == 401

    key = client.post("/api/settings/upload-key", headers=H).json()["key"]
    assert key.startswith("pvu_")
    assert client.get("/api/settings").json()["upload_key"]["exists"] is True

    auth = {"Authorization": f"Bearer {key}"}
    r = anon.post("/api/scans", params={"filename": "rec.mp4"}, content=body, headers=auth)
    assert r.status_code == 201, r.text
    # Same recording again (e.g. game closed twice): detected, not duplicated.
    r = anon.post("/api/scans", params={"filename": "rec.mp4"}, content=body, headers=auth)
    assert r.status_code == 200 and r.json()["duplicate"] is True

    # The key grants nothing else.
    assert anon.get("/api/pokemon", headers=auth).status_code == 401
    assert anon.get("/api/scans", headers=auth).status_code == 401
    assert anon.post("/api/privacy/delete-all", json={"confirm": "DELETE ALL"}, headers=auth).status_code == 401
    assert client.get("/api/settings").json()["upload_key"]["last_used_at"] is not None
    # The key hash is never exported.
    assert "upload_key" not in client.get("/api/privacy/export").text

    # Rotation invalidates the old key; revocation disables uploads.
    new_key = client.post("/api/settings/upload-key", headers=H).json()["key"]
    assert anon.post("/api/scans", params={"filename": "x.mp4"}, content=body, headers=auth).status_code == 401
    client.delete("/api/settings/upload-key", headers=H)
    r = anon.post("/api/scans", params={"filename": "x.mp4"}, content=body, headers={"Authorization": f"Bearer {new_key}"})
    assert r.status_code == 401


def test_auto_confirm_only_takes_ready_items(client, tmp_path):
    from app.jobs.worker import run_one

    assert client.put("/api/settings", json={"auto_confirm_ready": True}, headers=H).json()["auto_confirm_ready"] is True
    video = tmp_path / "rec.mp4"
    write_video(video)
    scan_id = client.post("/api/scans", params={"filename": "rec.mp4"}, content=video.read_bytes(),
                          headers={**H, "Content-Type": "video/mp4"}).json()["scan"]["id"]
    assert run_one("t")
    scan = client.get(f"/api/scans/{scan_id}").json()
    auto = scan["summary"]["auto_confirmed"]
    assert auto >= 15
    statuses = {g["status"] for g in scan["groups"]}
    assert "ready" not in statuses                 # everything ready was taken
    assert "needs_review" in statuses              # uncertain items still wait for the owner

    pokes = client.get("/api/pokemon").json()
    assert len(pokes) == auto
    assert all(p["confirmed_via"] == "auto" and p["user_confirmed"] is False for p in pokes)
    assert client.get("/api/dashboard").json()["auto_unchecked"] == auto

    checked = client.post(f"/api/pokemon/{pokes[0]['id']}/mark-checked", headers=H).json()
    assert checked["user_confirmed"] is True
    assert len(client.get("/api/pokemon?checked=no").json()) == auto - 1


def test_auto_confirm_off_by_default(client):
    assert client.get("/api/settings").json()["auto_confirm_ready"] is False


def _scan(client, path):
    from app.jobs.worker import run_one

    r = client.post("/api/scans", params={"filename": path.name}, content=path.read_bytes(),
                    headers={**H, "Content-Type": "video/mp4"})
    assert r.status_code == 201, r.text
    assert run_one("t")
    return client.get(f"/api/scans/{r.json()['scan']['id']}").json()


def test_auto_link_resightings_only_when_unambiguous(client, tmp_path):
    from tests.synthetic import DEFAULT_GRID, Mon

    # First recording: confirm everything that's ready by hand (includes the identical Bidoof pair).
    first = tmp_path / "first.mp4"
    write_video(first, include_detail=False)
    s1 = _scan(client, first)
    client.post(f"/api/scans/{s1['id']}/confirm-ready", headers=H)
    before = client.get("/api/pokemon").json()
    n_before = len(before)
    assert sum(1 for p in before if p["species_id"] == "bidoof") == 2

    # Second, different recording of mostly the same Pokémon, plus one new one, with automation on.
    client.put("/api/settings", json={"auto_confirm_ready": True}, headers=H)
    second = tmp_path / "second.mp4"
    newcomer = Mon("ponyta", "Ponyta", 31, (9, 9, 9))  # Ponyta has a Galarian form: grid can't tell -> review
    mons = [Mon("snorlax", "Snorlax", 31, (15, 15, 15))] + DEFAULT_GRID[1:12] + [newcomer]
    write_video(second, mons=mons, include_detail=False)
    s2 = _scan(client, second)
    by_key = {g["track_key"]: g for g in s2["groups"]}
    summary = s2["summary"]

    after = client.get("/api/pokemon").json()
    # Nothing was duplicated: re-sightings were linked, only genuinely new confident items were added.
    linked = [g for g in s2["groups"] if g["link_method"] == "auto-link"]
    added = [g for g in s2["groups"] if g["link_method"] == "auto"]
    assert summary["auto_linked"] == len(linked) >= 6
    assert len(after) == n_before + len(added)
    for g in linked:
        assert g["pokemon_id"] in {p["id"] for p in before}

    # The identical Bidoof pair: two existing matches -> never auto-linked, stays in Review.
    bidoofs = [g for g in s2["groups"] if (g["attributes"].get("species_id") or {}).get("value") == "bidoof"]
    assert len(bidoofs) == 2 and all(g["status"] == "needs_review" for g in bidoofs)
    # The nickname-only Pikachu ("Sparky") and the form-ambiguous Ponyta also wait for the owner.
    for g in s2["groups"]:
        a = g["attributes"]
        if (a.get("nickname") or {}).get("value") == "Sparky" or (a.get("species_id") or {}).get("value") == "ponyta":
            assert g["status"] == "needs_review", g
    # Snorlax at a different CP is not the same individual as the stored Snorlax.
    assert not any(g["link_method"] == "auto-link" and g["attributes"]["species_id"]["value"] == "snorlax" for g in s2["groups"])

    # Linking never downgrades a Pokémon the owner already confirmed.
    linked_p = client.get(f"/api/pokemon/{linked[0]['pokemon_id']}").json()
    assert linked_p["user_confirmed"] is True and linked_p["confirmed_via"] == "bulk"
    assert any(e["kind"] == "auto-link" for e in linked_p["evidence"])


def test_no_auto_link_when_setting_off(client, tmp_path):
    first = tmp_path / "a.mp4"
    write_video(first, include_detail=False)
    s1 = _scan(client, first)
    client.post(f"/api/scans/{s1['id']}/confirm-ready", headers=H)
    from tests.synthetic import DEFAULT_GRID

    second = tmp_path / "b.mp4"
    write_video(second, mons=list(reversed(DEFAULT_GRID[:12])), include_detail=False)
    s2 = _scan(client, second)
    assert s2["summary"]["auto_linked"] == 0
    assert not any(g["link_method"] for g in s2["groups"])
