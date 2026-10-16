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
    added = [g for g in s2["groups"] if g["link_method"] == "auto"]
    # The storage grid only shows species + CP. That is never enough to auto-link (review finding 1):
    # every re-sighting must wait for the owner, and none may be auto-added as a duplicate either.
    assert summary["auto_linked"] == 0
    assert not any(g["link_method"] == "auto-link" for g in s2["groups"])
    resightings = [g for g in s2["groups"] if g["candidates"]]
    assert len(resightings) >= 6 and all(g["status"] == "needs_review" for g in resightings)
    assert len(after) == n_before + len(added)
    # The genuinely new, confident Snorlax (different CP) is the kind of item that is auto-added.
    assert any(g["attributes"]["species_id"]["value"] == "snorlax" for g in added)
    for g in s2["groups"]:
        a = g["attributes"]
        if (a.get("nickname") or {}).get("value") == "Sparky" or (a.get("species_id") or {}).get("value") == "ponyta":
            assert g["status"] == "needs_review", g


def _group(db, scan, key, **vals):
    from app.models import ObservationGroup

    attrs = {k: {"value": v, "confidence": 0.99, "method": "test", "frame_id": None} for k, v in vals.items()}
    g = ObservationGroup(scan_id=scan.id, track_key=key, attributes=attrs, status="needs_review", review_reasons=[])
    db.add(g)
    db.flush()
    return g


@pytest.mark.parametrize("seen, stored, should_link", [
    # species + CP only, other fields missing on one or both sides: NOT enough (finding 1 repro)
    ({}, {}, False),
    ({"hp": 60}, {}, False),
    ({}, {"hp": 60, "weight_kg": 1.5, "height_m": 0.3}, False),
    # a positive corroborating match on either HP, weight+height, or verified IVs: linked
    ({"hp": 60}, {"hp": 60}, True),
    ({"weight_kg": 1.5, "height_m": 0.3}, {"weight_kg": 1.5, "height_m": 0.3}, True),
    ({"ivs": [1, 2, 3]}, {"iv_atk": 1, "iv_def": 2, "iv_sta": 3, "iv_status": "verified"}, True),
    # weight alone (height missing) isn't a full fingerprint
    ({"weight_kg": 1.5}, {"weight_kg": 1.5, "height_m": 0.3}, False),
])
def test_auto_link_requires_positive_evidence(fresh_db, seen, stored, should_link):
    from app.catalog.importer import auto_link_resightings, refresh_candidates
    from app.models import Pokemon, Scan

    with fresh_db.SessionLocal() as db:
        scan = Scan(original_filename="t.mp4", sha256="t", size_bytes=1)
        db.add(scan)
        p = Pokemon(species_id="pidgey", dex=16, cp=500, user_confirmed=True, **stored)
        db.add(p)
        db.flush()
        g = _group(db, scan, "k1", species_id="pidgey", dex=16, cp=500, **seen)
        refresh_candidates(db, g)
        assert [c["pokemon_id"] for c in g.candidate_pokemon_ids] == [p.id]
        linked = auto_link_resightings(db, scan.id)
        assert (linked == [p.id]) is should_link
        assert (g.status == "confirmed") is should_link


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


def test_link_matches_button_links_only_clear_resightings(client, tmp_path):
    from tests.synthetic import DEFAULT_GRID

    first = tmp_path / "one.mp4"
    write_video(first)  # grid + Bulbasaur detail (HP) + appraisal
    s1 = _scan(client, first)
    client.post(f"/api/scans/{s1['id']}/confirm-ready", headers=H)
    n_vault = len(client.get("/api/pokemon").json())

    second = tmp_path / "two.mp4"
    write_video(second, mons=DEFAULT_GRID[:12])  # same Pokémon again, including Bulbasaur's detail + appraisal
    s2 = _scan(client, second)
    linkable = s2["linkable"]
    clear = [g for g in s2["groups"] if g["clear_match"]]
    assert linkable == len(clear) >= 1
    assert all(g["candidates"] and len(g["candidates"]) == 1 for g in clear)
    # Grid-only re-sightings (species + CP only) are not "clear".
    assert any(g["candidates"] and not g["clear_match"] for g in s2["groups"])

    r = client.post(f"/api/scans/{s2['id']}/link-matches", headers=H).json()
    assert r["linked"] == linkable
    after = client.get(f"/api/scans/{s2['id']}").json()
    assert after["linkable"] == 0
    linked = [g for g in after["groups"] if g["link_method"] == "link"]
    assert {g["id"] for g in linked} == {g["id"] for g in clear}
    assert len(client.get("/api/pokemon").json()) == n_vault  # nothing duplicated
    p = client.get(f"/api/pokemon/{linked[0]['pokemon_id']}").json()
    assert p["user_confirmed"] is True and any(e["kind"] == "link" for e in p["evidence"])
