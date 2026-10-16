"""Full vertical slice on a SYNTHETIC recording (not game footage):
upload → FFmpeg → keyframes → OCR → grouping → review → confirm → catalog → CSV,
plus safe repeated import of the same recording.
"""
import csv
import io
import os

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


def _upload(c, path, **params):
    with open(path, "rb") as fh:
        return c.post("/api/scans", params={"filename": "IMG_0001.MP4", **params}, content=fh.read(),
                      headers={**H, "Content-Type": "video/mp4"})


def _run_worker():
    from app.jobs.worker import run_one

    assert run_one("test-worker")


def test_requires_auth_and_csrf_header(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/pokemon").status_code == 401
        c.post("/api/auth/login", json={"password": "test-password"})
        assert c.post("/api/demo/load").status_code == 403  # missing header
        assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_rejects_non_video(client):
    r = client.post("/api/scans", params={"filename": "x.mp4"}, content=b"not a video at all", headers=H)
    assert r.status_code == 415
    r = client.post("/api/scans", params={"filename": "x.gif"}, content=b"GIF89a", headers=H)
    assert r.status_code == 415


def test_vertical_slice_and_repeated_import(client, tmp_path):
    video = tmp_path / "synthetic.mp4"
    mons = write_video(video)

    r = _upload(client, video)
    assert r.status_code == 201, r.text
    scan_id = r.json()["scan"]["id"]
    _run_worker()

    scan = client.get(f"/api/scans/{scan_id}").json()
    assert scan["status"] in ("review", "done"), scan
    assert scan["video_stored"] is False  # raw video deleted by default
    groups = scan["groups"]
    # 24 grid cells; the detail + appraisal views fold into the Bulbasaur slot.
    assert len(groups) == len(mons), [(g["track_key"], g["attributes"].get("cp")) for g in groups]
    by_cp = {}
    for g in groups:
        by_cp.setdefault(g["attributes"]["cp"]["value"], []).append(g)
    # Two Bidoof with identical CP are two individuals.
    assert len(by_cp[mons[1].cp]) == 2
    # Nickname without other evidence: species stays unknown, needs review.
    sparky = by_cp[mons[6].cp][0]
    assert "species_id" not in sparky["attributes"] and sparky["status"] == "needs_review"
    # Bulbasaur: grid + detail + appraisal => HP read and IVs verified via bars + CP/HP feasibility.
    bulba = by_cp[mons[0].cp][0]
    assert bulba["attributes"]["species_id"]["value"] == "bulbasaur"
    assert bulba["attributes"]["hp"]["value"] == mons[0].hp
    assert bulba["attributes"]["ivs"]["value"] == list(mons[0].ivs)
    # Every attribute references a supporting frame.
    for g in groups:
        for k, a in g["attributes"].items():
            assert a.get("frame_id") is not None, (g["track_key"], k)

    # Evidence frame is served privately.
    img = client.get(f"/api/frames/{bulba['best_frame_id']}/image")
    assert img.status_code == 200 and img.headers["cache-control"] == "private, no-store"

    # Correct the nickname'd Pokémon, then confirm everything that is ready.
    r = client.post(f"/api/groups/{sparky['id']}/confirm", json={"edits": {"species_id": "pikachu"}}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["pokemon"]["nickname"] == "Sparky"
    r = client.post(f"/api/scans/{scan_id}/confirm-ready", headers=H)
    confirmed_bulk = r.json()["confirmed"]
    assert confirmed_bulk >= 15
    pokes = client.get("/api/pokemon?include_demo=false").json()
    n_before = len(pokes)
    assert n_before == confirmed_bulk + 1
    unresolved = [(g["track_key"], g["status"], g["review_reasons"], g["candidates"]) for g in
                  client.get(f"/api/scans/{scan_id}").json()["groups"] if g["status"] != "confirmed"]
    bulba_p = [p for p in pokes if p["species_id"] == "bulbasaur"]
    assert bulba_p, unresolved
    bulba_p = bulba_p[0]
    assert bulba_p["iv_status"] == "verified" and bulba_p["iv_atk"] == mons[0].ivs[0]

    # CSV export
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/collection.csv").text)))
    assert len(rows) == n_before
    assert all(row["shiny"] == "" for row in rows)  # unknown, never "no"

    # Repeated import of the identical recording: detected, nothing duplicated.
    r = _upload(client, video)
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert len(client.get("/api/pokemon?include_demo=false").json()) == n_before

    # Explicit reprocess: confirmed individuals are re-linked, not duplicated.
    r = _upload(client, video, reprocess="true")
    assert r.status_code == 201 and r.json()["reprocessed"] is True
    _run_worker()
    scan2 = client.get(f"/api/scans/{scan_id}").json()
    assert scan2["summary"]["relinked"] == n_before
    assert len(client.get("/api/pokemon?include_demo=false").json()) == n_before

    # Pending items now have the confirmed Pokémon as candidates, so they can't be auto-confirmed as new.
    pending = [g for g in scan2["groups"] if g["status"] in ("needs_review", "ready")]
    for g in pending:
        if g["candidates"]:
            r = client.post(f"/api/groups/{g['id']}/confirm", json={}, headers=H)
            assert r.status_code == 409


def test_cancel_running_job(client, tmp_path):
    from app import db as dbmod
    from app.jobs import queue
    from app.models import Job

    video = tmp_path / "synthetic.mp4"
    write_video(video, include_detail=False)
    scan_id = _upload(client, video).json()["scan"]["id"]
    with dbmod.SessionLocal() as db:
        job = queue.claim_next(db, "w-test")
    assert client.post(f"/api/scans/{scan_id}/cancel", headers=H).status_code == 200
    from app.pipeline import process_scan

    ctx = queue.JobContext(dbmod.SessionLocal, job.id, job.lease_token)
    with pytest.raises(queue.JobCancelled):
        process_scan(ctx, scan_id)
    with dbmod.SessionLocal() as db:
        queue.finish(db, job.id, job.lease_token, "cancelled", retryable=False)
        assert db.get(Job, job.id).status == "cancelled"
    scan = client.get(f"/api/scans/{scan_id}").json()
    assert scan["status"] == "cancelled"
    assert scan["video_stored"] is True  # kept briefly so Retry works; swept after 24h
    assert client.post(f"/api/scans/{scan_id}/retry", headers=H).status_code == 200


def test_delete_all_requires_phrase(client):
    client.post("/api/demo/load", headers=H)
    assert client.post("/api/privacy/delete-all", json={"confirm": "yes"}, headers=H).status_code == 400
    assert client.post("/api/privacy/delete-all", json={"confirm": "DELETE ALL"}, headers=H).status_code == 200
    assert client.get("/api/pokemon").json() == []
