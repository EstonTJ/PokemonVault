"""Regression tests for the architecture review: crash-safe reprocessing (2), shared validation (4) and
database-enforced uniqueness (5). Findings 1 and 3 are covered in test_automation.py and test_queue.py."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from tests.synthetic import write_video

H = {"X-PokeVault": "1"}


@pytest.fixture()
def client(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"password": "test-password"}).status_code == 200
        yield c


def _upload(c, path, **params):
    return c.post("/api/scans", params={"filename": "rec.mp4", **params}, content=path.read_bytes(),
                  headers={**H, "Content-Type": "video/mp4"})


def _run():
    from app.jobs.worker import run_one

    assert run_one("t")


def _published(scan_json):
    return {g["track_key"]: g for g in scan_json["groups"]}


# ------------------------------------------------------------------ finding 2: crash-safe reprocessing

def test_failed_reprocess_keeps_previous_results_and_decisions(client, tmp_path, monkeypatch):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    scan_id = _upload(client, video).json()["scan"]["id"]
    _run()
    first = client.get(f"/api/scans/{scan_id}").json()
    groups = list(_published(first).values())
    confirm_me, reject_me = groups[1], groups[2]
    assert client.post(f"/api/groups/{confirm_me['id']}/confirm", json={"identity": "new"} if confirm_me["candidates"] else {},
                       headers=H).status_code == 200
    assert client.post(f"/api/groups/{reject_me['id']}/reject", headers=H).status_code == 200
    frame_ids = {g["best_frame_id"] for g in groups}

    # Reprocess, and crash after the new attempt has written frames and observations.
    import app.pipeline as pipeline

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated crash mid-reprocess")

    monkeypatch.setattr(pipeline, "group_observations", boom)
    assert _upload(client, video, reprocess="true").status_code == 201
    _run()

    after = client.get(f"/api/scans/{scan_id}").json()
    # Previous results are still published, with the decisions intact.
    assert set(_published(after)) == set(_published(first))
    assert _published(after)[confirm_me["track_key"]]["status"] == "confirmed"
    assert _published(after)[reject_me["track_key"]]["status"] == "rejected"
    # Evidence images of the published results were not deleted.
    for fid in frame_ids:
        assert client.get(f"/api/frames/{fid}/image").status_code == 200
    # Nothing from the crashed attempt is visible or left behind.
    from app import db as dbmod
    from app.models import Frame, Observation, ObservationGroup, Scan

    with dbmod.SessionLocal() as db:
        current = db.get(Scan, scan_id).current_attempt_id
        for model in (Frame, Observation, ObservationGroup):
            stray = db.query(model).filter(model.scan_id == scan_id)
            stray = stray.filter(model.attempt_id != current) if current else stray.filter(model.attempt_id.isnot(None))
            assert stray.count() == 0, model.__name__

    # A later successful run publishes atomically and carries both decisions over.
    monkeypatch.undo()
    job_status = client.get(f"/api/scans/{scan_id}").json()["job"]["status"]
    if job_status == "queued":  # the crash was retryable, so the job was re-queued
        _run()
    else:
        assert client.post(f"/api/scans/{scan_id}/retry", headers=H).status_code == 200
        _run()
    final = client.get(f"/api/scans/{scan_id}").json()
    assert _published(final)[confirm_me["track_key"]]["status"] == "confirmed"
    assert _published(final)[reject_me["track_key"]]["status"] == "rejected"
    assert final["summary"]["relinked"] == 1


def test_decision_made_during_processing_is_not_lost(client, tmp_path, monkeypatch):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    scan_id = _upload(client, video).json()["scan"]["id"]
    _run()
    target = list(_published(client.get(f"/api/scans/{scan_id}").json()).values())[3]

    import app.pipeline as pipeline

    real = pipeline.group_observations

    def owner_confirms_meanwhile(obs, *args, **kwargs):
        # While the attempt is processing, the owner confirms an item on the still-published results.
        r = client.post(f"/api/groups/{target['id']}/confirm",
                        json={"identity": "new"} if target["candidates"] else {}, headers=H)
        assert r.status_code == 200
        return real(obs, *args, **kwargs)

    monkeypatch.setattr(pipeline, "group_observations", owner_confirms_meanwhile)
    assert _upload(client, video, reprocess="true").status_code == 201
    _run()
    final = client.get(f"/api/scans/{scan_id}").json()
    assert _published(final)[target["track_key"]]["status"] == "confirmed"
    assert final["summary"]["relinked"] == 1


# ------------------------------------------------------------------ finding 4: validation on every path

@pytest.mark.parametrize("body", [
    {"species_id": "pidgey", "cp": -1},
    {"species_id": "pidgey", "cp": 99999},
    {"species_id": "pidgey", "iv_atk": 99, "iv_def": 99, "iv_sta": 99},
    {"species_id": "pidgey", "iv_atk": 10},                      # partial IVs
    {"species_id": "pidgey", "cp": "500"},                       # wrong type
    {"species_id": "pidgey", "shiny": "yes"},
    {"species_id": "not-a-pokemon"},
    {"cp": 500},                                                 # species required
    {"species_id": "pidgey", "iv_status": "verified"},           # not an editable field
    {"species_id": "pidgey", "shadow": True, "purified": True},
])
def test_manual_create_rejects_invalid(client, body):
    r = client.post("/api/pokemon", json=body, headers=H)
    assert r.status_code == 400, r.text
    assert client.get("/api/pokemon").json() == []


def test_manual_create_valid_and_verified_only_with_all_ivs(client):
    r = client.post("/api/pokemon", json={"species_id": "pidgey", "cp": 500, "iv_atk": 1, "iv_def": 2, "iv_sta": 3},
                    headers=H)
    assert r.status_code == 201 and r.json()["iv_status"] == "verified"
    r = client.post("/api/pokemon", json={"species_id": "pidgey", "cp": 400}, headers=H)
    assert r.status_code == 201 and r.json()["iv_status"] == "unknown"


def test_collection_edit_rejects_invalid(client):
    pid = client.post("/api/pokemon", json={"species_id": "pidgey", "cp": 500}, headers=H).json()["id"]
    for bad in ({"cp": -1}, {"iv_atk": 99, "iv_def": 1, "iv_sta": 1}, {"iv_atk": 5}, {"hp": 0}, {"bogus": 1}):
        assert client.patch(f"/api/pokemon/{pid}", json=bad, headers=H).status_code == 400, bad
    assert client.get(f"/api/pokemon/{pid}").json()["cp"] == 500


def test_review_edits_and_confirm_use_same_rules(client, tmp_path):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    scan_id = _upload(client, video).json()["scan"]["id"]
    _run()
    g = list(_published(client.get(f"/api/scans/{scan_id}").json()).values())[0]
    assert client.patch(f"/api/groups/{g['id']}", json={"cp": -1}, headers=H).status_code == 400
    assert client.patch(f"/api/groups/{g['id']}", json={"iv_atk": 99, "iv_def": 99, "iv_sta": 99},
                        headers=H).status_code == 400
    r = client.post(f"/api/groups/{g['id']}/confirm", json={"identity": "new", "edits": {"cp": 99999}}, headers=H)
    assert r.status_code == 400
    assert client.get("/api/pokemon").json() == []


def test_database_constraints_are_the_backstop(fresh_db):
    from app.models import Pokemon

    bad_rows = [dict(cp=-1), dict(hp=5), dict(iv_atk=99, iv_def=1, iv_sta=1),
                dict(iv_status="verified"), dict(iv_status="bogus")]
    for vals in bad_rows:
        with fresh_db.SessionLocal() as db:
            db.add(Pokemon(species_id="pidgey", dex=16, **vals))
            with pytest.raises(IntegrityError):
                db.commit()


# ------------------------------------------------------------------ finding 5: database-enforced uniqueness

def test_one_scan_per_recording_and_one_active_job_per_scan(fresh_db):
    from app.jobs import queue
    from app.models import Job, Scan

    with fresh_db.SessionLocal() as db:
        db.add(Scan(original_filename="a", sha256="same", size_bytes=1))
        db.commit()
        db.add(Scan(original_filename="b", sha256="same", size_bytes=1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # Demo fixtures share a placeholder hash and are exempt.
        db.add_all([Scan(original_filename="d1", sha256="demo", size_bytes=0, is_demo=True),
                    Scan(original_filename="d2", sha256="demo", size_bytes=0, is_demo=True)])
        db.commit()

        scan = db.query(Scan).filter_by(sha256="same").one()
        queue.enqueue(db, scan)
        db.commit()
        with pytest.raises(IntegrityError):
            queue.enqueue(db, scan)
        db.rollback()
        # Finished jobs don't block a new one.
        db.query(Job).filter_by(scan_id=scan.id).update({"status": "succeeded"})
        db.commit()
        queue.enqueue(db, scan)
        db.commit()


def test_racing_identical_upload_returns_duplicate_not_error(client, tmp_path, monkeypatch):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    first = _upload(client, video)
    assert first.status_code == 201

    # Simulate the race: the second request's duplicate check runs before the first one is visible.
    import app.api.scans as scans_api

    real = scans_api._scan_by_sha
    calls = {"n": 0}

    def not_yet_visible(db, digest):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, digest)

    monkeypatch.setattr(scans_api, "_scan_by_sha", not_yet_visible)
    second = _upload(client, video)
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert len(client.get("/api/scans").json()) == 1


def test_reprocess_while_processing_is_refused_and_video_untouched(client, tmp_path):
    video = tmp_path / "rec.mp4"
    write_video(video, include_detail=False)
    scan = _upload(client, video).json()["scan"]  # job is queued, not yet run
    from app import db as dbmod
    from app.models import Scan

    with dbmod.SessionLocal() as db:
        path = db.get(Scan, scan["id"]).video_path
    before = os.path.getmtime(path)
    r = _upload(client, video, reprocess="true")
    assert r.status_code == 409
    assert os.path.exists(path) and os.path.getmtime(path) == before
    assert client.post(f"/api/scans/{scan['id']}/retry", headers=H).status_code == 409
    assert client.post(f"/api/scans/{scan['id']}/purge-frames", headers=H).status_code == 409
