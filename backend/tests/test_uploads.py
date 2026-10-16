"""Resumable chunked uploads."""
import hashlib
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.synthetic import write_video

H = {"X-PokeVault": "1"}
CHUNK = 256 * 1024  # small chunks so the ~1.5 MB synthetic video has several


@pytest.fixture()
def client(fresh_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(type(settings), "upload_chunk_bytes", property(lambda self: CHUNK))
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"password": "test-password"}).status_code == 200
        yield c


@pytest.fixture()
def video(tmp_path):
    p = tmp_path / "rec.mp4"
    write_video(p, include_detail=False)
    return p.read_bytes()


def _start(c, data, **extra):
    r = c.post("/api/uploads", json={"filename": "rec.mp4", "size": len(data), "last_modified": 111, **extra}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _put(c, sid, i, data, sha=True, body=None):
    piece = data[i * CHUNK:(i + 1) * CHUNK] if body is None else body
    headers = {**H, "Content-Type": "application/octet-stream"}
    if sha:
        headers["X-Chunk-SHA256"] = hashlib.sha256(piece).hexdigest()
    return c.put(f"/api/uploads/{sid}/chunks/{i}", content=piece, headers=headers)


def test_chunked_upload_out_of_order_with_repeats_creates_scan(client, video):
    s = _start(client, video)
    n = s["total_chunks"]
    assert n >= 3 and s["received"] == [] and s["resumed"] is False
    order = list(range(n))[::-1] + [0, 1]  # reversed, then two chunks re-sent
    for i in order:
        assert _put(client, s["id"], i, video).status_code == 200
    r = client.post(f"/api/uploads/{s['id']}/complete", headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate"] is False and body["scan"]["size_bytes"] == len(video)
    assert body["scan"]["job"]["status"] == "queued"

    from app import db as dbmod
    from app.models import Scan

    with dbmod.SessionLocal() as db:
        scan = db.get(Scan, body["scan"]["id"])
        assert scan.sha256 == hashlib.sha256(video).hexdigest()
        with open(scan.video_path, "rb") as fh:
            assert fh.read() == video  # reassembled byte-for-byte
    assert client.get(f"/api/uploads/{s['id']}").json()["status"] == "complete"


def test_resume_returns_same_session_with_received_chunks(client, video):
    s = _start(client, video)
    for i in (0, 2):
        assert _put(client, s["id"], i, video).status_code == 200
    again = _start(client, video)  # e.g. after a page reload, same file picked again
    assert again["id"] == s["id"] and again["resumed"] is True and again["received"] == [0, 2]
    # A different file (different size) gets a fresh session.
    other = _start(client, video + b"x")
    assert other["id"] != s["id"]


def test_corrupted_short_and_oversized_chunks_are_rejected(client, video):
    s = _start(client, video)
    good = video[:CHUNK]
    bad = bytes([good[0] ^ 1]) + good[1:]
    r = client.put(f"/api/uploads/{s['id']}/chunks/0", content=bad,
                   headers={**H, "X-Chunk-SHA256": hashlib.sha256(good).hexdigest()})
    assert r.status_code == 422
    assert _put(client, s["id"], 0, video, body=good[:-10]).status_code == 400
    assert _put(client, s["id"], 0, video, body=good + b"extra").status_code == 413
    assert _put(client, s["id"], 99, video, body=good).status_code == 400
    assert client.get(f"/api/uploads/{s['id']}").json()["received"] == []


def test_complete_with_missing_chunks_lists_them(client, video):
    s = _start(client, video)
    _put(client, s["id"], 0, video)
    r = client.post(f"/api/uploads/{s['id']}/complete", headers=H)
    assert r.status_code == 409
    assert r.json()["detail"]["missing"] == list(range(1, s["total_chunks"]))


def _upload_all(c, data, **extra):
    s = _start(c, data, **extra)
    for i in range(s["total_chunks"]):
        assert _put(c, s["id"], i, data).status_code == 200
    return s, c.post(f"/api/uploads/{s['id']}/complete", headers=H)


def test_duplicate_recording_and_non_video(client, video):
    _, first = _upload_all(client, video)
    assert first.status_code == 201
    s2, second = _upload_all(client, video, last_modified=222)
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert client.get(f"/api/uploads/{s2['id']}").json()["status"] == "complete"
    # Reprocessing while the first job is still queued is refused, and the session is cleaned up.
    s3, third = _upload_all(client, video, last_modified=333, reprocess=True)
    assert third.status_code == 409
    assert client.get(f"/api/uploads/{s3['id']}").status_code == 404
    assert not os.path.exists(os.path.join(_partial_dir(), f"{s3['id']}.part"))

    junk = b"\x00" * (CHUNK + 10)
    s4, r4 = _upload_all(client, junk, filename="junk.mp4")
    assert r4.status_code == 415


def _partial_dir():
    from app.config import settings

    return str(settings.partial_dir)


def test_limits_and_validation_at_start(client):
    from app.config import settings

    too_big = settings.max_upload_bytes + 1
    assert client.post("/api/uploads", json={"filename": "a.mp4", "size": too_big}, headers=H).status_code == 413
    assert client.post("/api/uploads", json={"filename": "a.gif", "size": 10}, headers=H).status_code == 415
    assert client.post("/api/uploads", json={"filename": "a.mp4", "size": "10"}, headers=H).status_code == 400
    for n in range(settings.max_open_upload_sessions):
        assert client.post("/api/uploads", json={"filename": f"{n}.mp4", "size": 10}, headers=H).status_code == 200
    assert client.post("/api/uploads", json={"filename": "x.mp4", "size": 10}, headers=H).status_code == 429


def test_abort_and_expiry_remove_partial_files(client, video):
    s = _start(client, video)
    _put(client, s["id"], 0, video)
    part = os.path.join(_partial_dir(), f"{s['id']}.part")
    assert os.path.exists(part)
    assert client.delete(f"/api/uploads/{s['id']}", headers=H).status_code == 200
    assert not os.path.exists(part)
    assert _put(client, s["id"], 1, video).status_code == 404

    s2 = _start(client, video, last_modified=999)
    part2 = os.path.join(_partial_dir(), f"{s2['id']}.part")
    from app import db as dbmod
    from app.api.uploads import sweep_expired_uploads
    from app.models import UploadSession, utcnow

    with dbmod.SessionLocal() as db:
        db.get(UploadSession, s2["id"]).updated_at = utcnow() - timedelta(days=2)
        db.commit()
    assert sweep_expired_uploads() == 1
    assert not os.path.exists(part2)
    with dbmod.SessionLocal() as db:
        db.get(UploadSession, s["id"]).updated_at = utcnow() - timedelta(days=2)  # the aborted one above
        db.commit()
    sweep_expired_uploads()
    with dbmod.SessionLocal() as db:
        assert db.get(UploadSession, s["id"]) is None and db.get(UploadSession, s2["id"]) is None


def test_requires_owner_and_csrf(fresh_db):
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/api/uploads", json={"filename": "a.mp4", "size": 10}, headers=H).status_code == 401
        c.post("/api/auth/login", json={"password": "test-password"})
        assert c.post("/api/uploads", json={"filename": "a.mp4", "size": 10}).status_code == 403
