"""Resumable, chunked uploads (up to POKEVAULT_MAX_UPLOAD_MB, default 1 GB).

Flow:
  POST   /api/uploads                     start (or resume) a session -> id, chunk_size, received chunk indexes
  PUT    /api/uploads/{id}/chunks/{n}     send chunk n (raw bytes); X-Chunk-SHA256 is verified when given
  GET    /api/uploads/{id}                which chunks the server already has
  POST   /api/uploads/{id}/complete       verify, hash and register the recording (same rules as /api/scans)
  DELETE /api/uploads/{id}                abort and delete the partial file

Each chunk is a separate small request, so a dropped connection costs at most one chunk, and proxies never see
one huge, slow request. Chunks are written at their offset in a preallocated private file and recorded in
upload_chunks; re-sending a chunk is harmless.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import Owner
from ..config import settings
from ..db import get_db
from ..models import UploadChunk, UploadSession, utcnow
from .scans import check_upload_allowed, looks_like_video, register_recording

router = APIRouter(prefix="/api/uploads", dependencies=[Owner])

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _expired_before():
    return utcnow() - timedelta(hours=settings.upload_session_ttl_h)


def _session_json(db: Session, s: UploadSession) -> Dict[str, Any]:
    received = [i for (i,) in db.execute(select(UploadChunk.index).where(UploadChunk.upload_id == s.id)
                                         .order_by(UploadChunk.index))]
    return {"id": s.id, "filename": s.filename, "size": s.size_bytes, "chunk_size": s.chunk_size,
            "total_chunks": s.total_chunks, "received": received, "status": s.status,
            "max_upload_mb": settings.max_upload_mb}


def _open_session(db: Session, upload_id: str) -> UploadSession:
    s = db.get(UploadSession, upload_id)
    if s is None or s.status == "aborted" or s.updated_at < _expired_before():
        raise HTTPException(404, "Upload not found or expired. Start the upload again.")
    if s.status != "open":
        raise HTTPException(409, "This upload has already been completed.")
    return s


def _expected_len(s: UploadSession, index: int) -> int:
    if index == s.total_chunks - 1:
        return s.size_bytes - index * s.chunk_size
    return s.chunk_size


@router.post("")
def start(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    filename = str(body.get("filename") or "")[:255]
    size = body.get("size")
    kind = str(body.get("kind") or "auto")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise HTTPException(400, "size must be a positive integer (bytes).")
    check_upload_allowed(db, filename, kind, size)
    last_modified = body.get("last_modified")
    fingerprint = f"{filename}|{size}|{last_modified if isinstance(last_modified, (int, float)) else ''}"
    keep_video, reprocess = bool(body.get("keep_video")), bool(body.get("reprocess"))

    # Resume: the same file (name, size, modified time) with an unexpired open session picks up where it left off.
    existing = db.scalars(select(UploadSession).where(
        UploadSession.fingerprint == fingerprint, UploadSession.status == "open",
        UploadSession.updated_at >= _expired_before()).order_by(UploadSession.created_at.desc())).first()
    if existing is not None and os.path.exists(existing.part_path):
        existing.kind, existing.keep_video, existing.reprocess = kind, keep_video, reprocess
        existing.updated_at = utcnow()
        db.commit()
        return {**_session_json(db, existing), "resumed": True}

    open_count = db.query(UploadSession).filter(UploadSession.status == "open",
                                                UploadSession.updated_at >= _expired_before()).count()
    if open_count >= settings.max_open_upload_sessions:
        raise HTTPException(429, "Too many unfinished uploads. Finish or cancel one first.")

    settings.ensure_dirs()
    chunk = settings.upload_chunk_bytes
    s = UploadSession(filename=filename, fingerprint=fingerprint, size_bytes=size, chunk_size=chunk,
                      total_chunks=(size + chunk - 1) // chunk, kind=kind, keep_video=keep_video, reprocess=reprocess,
                      part_path="")
    db.add(s)
    db.flush()
    part = settings.partial_dir / f"{s.id}.part"
    # Preallocate (sparse) so chunks can be written at their offsets in any order.
    with open(part, "wb") as fh:
        fh.truncate(size)
    os.chmod(part, 0o600)
    s.part_path = str(part)
    db.commit()
    return {**_session_json(db, s), "resumed": False}


@router.get("/{upload_id}")
def status(upload_id: str, db: Session = Depends(get_db)):
    s = db.get(UploadSession, upload_id)
    if s is None or s.status == "aborted" or (s.status == "open" and s.updated_at < _expired_before()):
        raise HTTPException(404, "Upload not found or expired.")
    return _session_json(db, s)


@router.put("/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request, db: Session = Depends(get_db)):
    s = _open_session(db, upload_id)
    if not (0 <= index < s.total_chunks):
        raise HTTPException(400, "Chunk index out of range.")
    expected = _expected_len(s, index)
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > expected:
            raise HTTPException(413, "Chunk is larger than expected.")
    if len(data) != expected:
        raise HTTPException(400, f"Chunk {index} is incomplete ({len(data)} of {expected} bytes); resend it.")
    digest = hashlib.sha256(data).hexdigest()
    claimed = (request.headers.get("x-chunk-sha256") or "").lower()
    if claimed and (not _SHA_RE.match(claimed) or claimed != digest):
        raise HTTPException(422, f"Chunk {index} was corrupted in transit; resend it.")

    fd = os.open(s.part_path, os.O_WRONLY)
    try:
        os.pwrite(fd, bytes(data), index * s.chunk_size)
        os.fsync(fd)
    finally:
        os.close(fd)

    existing = db.get(UploadChunk, (upload_id, index))
    if existing is None:
        db.add(UploadChunk(upload_id=upload_id, index=index, size=len(data), sha256=digest))
    else:
        existing.size, existing.sha256 = len(data), digest
    s.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError:  # the same chunk arrived twice concurrently; the bytes are identical
        db.rollback()
    received = db.query(UploadChunk).filter(UploadChunk.upload_id == upload_id).count()
    return {"index": index, "received_count": received, "total_chunks": s.total_chunks}


@router.post("/{upload_id}/complete")
def complete(upload_id: str, db: Session = Depends(get_db)):
    s = _open_session(db, upload_id)
    have = {i for (i,) in db.execute(select(UploadChunk.index).where(UploadChunk.upload_id == upload_id))}
    missing: List[int] = [i for i in range(s.total_chunks) if i not in have]
    if missing:
        raise HTTPException(409, {"message": "Some chunks are missing.", "missing": missing[:200]})
    part = Path(s.part_path)
    if not part.exists() or part.stat().st_size != s.size_bytes:
        raise HTTPException(409, "The partial file is incomplete on the server. Start the upload again.")
    h = hashlib.sha256()
    with open(part, "rb") as fh:
        head = fh.read(16)
        h.update(head)
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    if not looks_like_video(head):
        _abort(db, s)
        raise HTTPException(415, "File does not look like a MOV/MP4 video.")
    ext = os.path.splitext(s.filename)[1].lower()
    # Mark complete first: register_recording takes ownership of the file and commits.
    s.status = "complete"
    db.query(UploadChunk).filter(UploadChunk.upload_id == upload_id).delete()
    db.flush()
    try:
        resp = register_recording(db, part, filename=s.filename, ext=ext, kind=s.kind, keep_video=s.keep_video,
                                  reprocess=s.reprocess, size=s.size_bytes, digest=h.hexdigest())
    except HTTPException:
        # Refused (e.g. this recording is already processing). The file is gone, so close the session out.
        db.rollback()
        s = db.get(UploadSession, upload_id)
        if s is not None:
            _abort(db, s)
        raise
    db.commit()  # covers the duplicate path, which returns without committing the session update
    return resp


@router.delete("/{upload_id}")
def abort(upload_id: str, db: Session = Depends(get_db)):
    s = db.get(UploadSession, upload_id)
    if s is not None and s.status == "open":
        _abort(db, s)
    return {"ok": True}


def _abort(db: Session, s: UploadSession) -> None:
    s.status = "aborted"
    db.query(UploadChunk).filter(UploadChunk.upload_id == s.id).delete()
    db.commit()
    try:
        os.remove(s.part_path)
    except FileNotFoundError:
        pass


def sweep_expired_uploads() -> int:
    """Delete partial files of uploads nobody touched within the TTL (called by the worker's periodic sweep)."""
    from .. import db as dbmod

    n = 0
    with dbmod.SessionLocal() as db:
        for s in db.scalars(select(UploadSession).where(UploadSession.status == "open",
                                                        UploadSession.updated_at < _expired_before())).all():
            _abort(db, s)
            n += 1
        # Bookkeeping rows of finished or cancelled uploads are only useful briefly.
        db.query(UploadSession).filter(UploadSession.status != "open",
                                       UploadSession.updated_at < _expired_before()).delete()
        db.commit()
    # Orphaned part files (e.g. a crash between creating the file and committing the session).
    known = set()
    with dbmod.SessionLocal() as db:
        known = {p for (p,) in db.execute(select(UploadSession.part_path).where(UploadSession.status == "open"))}
    if settings.partial_dir.exists():
        import time

        for f in settings.partial_dir.glob("*.part"):
            # Only clearly abandoned files: never race a session that is being created right now.
            if str(f) not in known and time.time() - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)
    return n
