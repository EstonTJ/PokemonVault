"""Upload, scan status, cancel/retry, review queue and evidence frames."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import timedelta
from typing import Any, Dict, List, Optional

import cv2
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Owner, require_owner_or_upload_key
from ..catalog import importer
from ..config import settings
from ..db import get_db
from ..jobs import queue
from ..models import Appraisal, Frame, Job, Observation, ObservationGroup, Pokemon, Scan, utcnow
from ..pipeline import discard_scan_media, frame_dir_for
from .serialize import group_json, pokemon_json, scan_json

router = APIRouter(prefix="/api", dependencies=[Owner])
# Upload accepts the owner session OR the upload-only key used by the iOS Shortcut.
upload_router = APIRouter(prefix="/api")

ALLOWED_EXT = {".mov", ".mp4", ".m4v"}
# ISO base media file format brands / top-level atoms used by MOV and MP4.
_ATOMS = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"}


def _latest_job(db: Session, scan_id: int) -> Optional[Job]:
    return db.scalars(select(Job).where(Job.scan_id == scan_id).order_by(Job.id.desc()).limit(1)).first()


@upload_router.post("/scans", status_code=201)
async def upload(request: Request, filename: str = Query(..., max_length=255), kind: str = Query("auto"),
                 keep_video: bool = Query(False), reprocess: bool = Query(False), db: Session = Depends(get_db),
                 auth_method: str = Depends(require_owner_or_upload_key)):
    """Raw-body upload (the browser sends the File as the request body) so limits apply while streaming."""
    if kind not in ("auto", "collection", "appraisal"):
        raise HTTPException(400, "kind must be auto, collection or appraisal.")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, "Only MOV and MP4 recordings are supported.")
    declared = request.headers.get("content-length")
    if declared and int(declared) > settings.max_upload_bytes:
        raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
    since = utcnow() - timedelta(hours=1)
    recent = db.query(Scan).filter(Scan.created_at >= since, Scan.is_demo.is_(False)).count()
    if recent >= settings.max_uploads_per_hour:
        raise HTTPException(429, "Upload limit reached for this hour.")
    active = db.query(Job).filter(Job.status.in_(("queued", "running"))).count()
    if active >= 3:
        raise HTTPException(429, "Several recordings are already processing; wait for them to finish.")

    settings.ensure_dirs()
    tmp = settings.upload_dir / f"incoming-{os.urandom(8).hex()}{ext}"
    h = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                h.update(chunk)
                fh.write(chunk)
        if size == 0:
            raise HTTPException(400, "Empty upload.")
        if head[4:8] not in _ATOMS:
            raise HTTPException(415, "File does not look like a MOV/MP4 video.")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, 0o600)
    digest = h.hexdigest()

    existing = db.scalars(select(Scan).where(Scan.sha256 == digest, Scan.is_demo.is_(False)).order_by(Scan.id)).first()
    if existing is not None and not reprocess:
        tmp.unlink(missing_ok=True)
        return JSONResponse({"duplicate": True, "scan": scan_json(existing, _latest_job(db, existing.id)),
                             "message": "This exact recording was already imported. Re-upload with 'reprocess' to scan it "
                                        "again; confirmed Pokémon will be re-linked, not duplicated."}, status_code=200)
    if existing is not None:
        active_job = _latest_job(db, existing.id)
        if active_job and active_job.status in ("queued", "running"):
            tmp.unlink(missing_ok=True)
            raise HTTPException(409, "This recording is already being processed.")
        scan = existing
        if scan.video_path and os.path.exists(scan.video_path):
            os.remove(scan.video_path)
    else:
        scan = Scan(original_filename=re.sub(r"[^\w.\- ]", "_", filename)[:255], sha256=digest, size_bytes=size, kind=kind)
        db.add(scan)
        db.flush()
    final = settings.upload_dir / f"scan-{scan.id}{ext}"
    shutil.move(str(tmp), final)
    scan.video_path = str(final)
    scan.video_deleted_at = None
    scan.keep_video = keep_video
    scan.kind = kind
    job = queue.enqueue(db, scan)
    db.commit()
    return {"duplicate": False, "reprocessed": existing is not None, "scan": scan_json(scan, job)}


@router.get("/scans")
def list_scans(db: Session = Depends(get_db)):
    scans = db.scalars(select(Scan).order_by(Scan.id.desc())).all()
    return [scan_json(s, _latest_job(db, s.id)) for s in scans]


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id) or _404()
    groups = db.scalars(select(ObservationGroup).where(ObservationGroup.scan_id == scan_id).order_by(ObservationGroup.first_ts_ms)).all()
    out = scan_json(s, _latest_job(db, scan_id))
    out["groups"] = [group_json(g) for g in groups]
    return out


@router.post("/scans/{scan_id}/cancel")
def cancel(scan_id: int, db: Session = Depends(get_db)):
    job = _latest_job(db, scan_id)
    if job is None or job.status not in ("queued", "running"):
        raise HTTPException(409, "Nothing to cancel.")
    queue.request_cancel(db, job)
    return {"ok": True, "job_status": job.status}


@router.post("/scans/{scan_id}/retry")
def retry(scan_id: int, db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id) or _404()
    job = _latest_job(db, scan_id)
    if job and job.status in ("queued", "running"):
        raise HTTPException(409, "Already processing.")
    if not s.video_path or not os.path.exists(s.video_path):
        raise HTTPException(410, "The raw video was deleted after processing. Upload the recording again to rescan it.")
    job = queue.enqueue(db, s)
    db.commit()
    return scan_json(s, job)


@router.post("/scans/{scan_id}/discard-video")
def discard_video(scan_id: int, db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id) or _404()
    s.keep_video = False
    db.commit()
    discard_scan_media(scan_id, keep_video_if_requested=False)
    return {"ok": True}


@router.post("/scans/{scan_id}/purge-frames")
def purge_frames(scan_id: int, db: Session = Depends(get_db)):
    db.get(Scan, scan_id) or _404()
    discard_scan_media(scan_id, keep_video_if_requested=True, frames_too=True)
    return {"ok": True}


@router.delete("/scans/{scan_id}")
def delete_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.get(Scan, scan_id) or _404()
    job = _latest_job(db, scan_id)
    if job and job.status == "running":
        raise HTTPException(409, "Cancel processing first.")
    if s.video_path and os.path.exists(s.video_path):
        os.remove(s.video_path)
    shutil.rmtree(frame_dir_for(scan_id), ignore_errors=True)
    db.delete(s)
    db.commit()
    return {"ok": True, "note": "Confirmed Pokémon from this scan are kept in your collection."}


# ------------------------------------------------------------------ review

@router.get("/review")
def review_queue(db: Session = Depends(get_db)):
    groups = db.scalars(select(ObservationGroup).where(ObservationGroup.status.in_(("needs_review", "ready")))
                        .order_by(ObservationGroup.scan_id.desc(), ObservationGroup.first_ts_ms)).all()
    return [group_json(g) for g in groups]


@router.get("/groups/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(ObservationGroup, group_id) or _404()
    out = group_json(g)
    obs = db.scalars(select(Observation).where(Observation.group_id == g.id)).all()
    out["observations"] = [{"id": o.id, "frame_id": o.frame_id, "screen_type": o.screen_type, "grid_col": o.grid_col,
                            "bbox": o.bbox, "attributes": o.attributes} for o in obs]
    frames = {f.id: f for f in db.scalars(select(Frame).where(Frame.id.in_([o.frame_id for o in obs]))).all()}
    out["frames"] = [{"id": f.id, "timestamp_ms": f.timestamp_ms, "screen_type": f.screen_type, "has_image": bool(f.image_path)}
                     for f in sorted(frames.values(), key=lambda f: f.timestamp_ms)]
    cands = []
    for c in g.candidate_pokemon_ids or []:
        p = db.get(Pokemon, c["pokemon_id"])
        if p:
            cands.append({**c, "pokemon": pokemon_json(p)})
    out["candidates"] = cands
    ap = db.scalars(select(Appraisal).where(Appraisal.group_id == g.id)).first()
    out["appraisal"] = None if ap is None else {"status": ap.status, "confidence": ap.confidence, "bars": ap.bars,
                                                 "iv_candidates": ap.iv_candidates[:64], "frame_id": ap.frame_id}
    return out


@router.patch("/groups/{group_id}")
def edit_group(group_id: int, edits: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    g = db.get(ObservationGroup, group_id) or _404()
    if g.status in ("confirmed", "rejected"):
        raise HTTPException(409, "Group already resolved; edit the Pokémon in your collection instead.")
    try:
        importer.apply_group_edits(db, g, edits)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    importer.refresh_candidates(db, g)
    g.status = importer.decide_status(g)
    db.commit()
    return group_json(g)


@router.post("/groups/{group_id}/confirm")
def confirm(group_id: int, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db)):
    g = db.get(ObservationGroup, group_id) or _404()
    if g.status in ("confirmed", "rejected"):
        raise HTTPException(409, f"Group already {g.status}.")
    decision = body.get("identity")  # "new" | "<pokemon id>" | None
    if g.candidate_pokemon_ids and decision is None:
        raise HTTPException(409, "Possible match with an existing Pokémon: choose 'new' or the matching Pokémon.")
    link_to = decision if decision not in (None, "new") else None
    try:
        p = importer.confirm_group(db, g, link_to=link_to, via="review", edits=body.get("edits"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    _refresh_scan_status(db, g.scan_id)
    return {"group": group_json(g), "pokemon": pokemon_json(p)}


@router.post("/groups/{group_id}/reject")
def reject(group_id: int, db: Session = Depends(get_db)):
    g = db.get(ObservationGroup, group_id) or _404()
    importer.reject_group(db, g)
    db.commit()
    _refresh_scan_status(db, g.scan_id)
    return group_json(g)


@router.post("/scans/{scan_id}/confirm-ready")
def confirm_ready(scan_id: int, db: Session = Depends(get_db)):
    ids = importer.bulk_confirm_ready(db, scan_id)
    db.commit()
    _refresh_scan_status(db, scan_id)
    return {"confirmed": len(ids)}


def _refresh_scan_status(db: Session, scan_id: int) -> None:
    s = db.get(Scan, scan_id)
    if s is None or s.status not in ("review", "done"):
        return
    pending = db.query(ObservationGroup).filter(ObservationGroup.scan_id == scan_id,
                                                ObservationGroup.status.in_(("needs_review", "ready"))).count()
    s.status = "review" if pending else "done"
    db.commit()


# ------------------------------------------------------------------ evidence frames

@router.get("/frames/{frame_id}/image")
def frame_image(frame_id: int, crop: Optional[str] = Query(None, pattern=r"^\d+(\.\d+)?(,\d+(\.\d+)?){3}$"),
                db: Session = Depends(get_db)):
    f = db.get(Frame, frame_id) or _404()
    if not f.image_path or not os.path.exists(f.image_path):
        raise HTTPException(410, "This frame image was not retained.")
    img = cv2.imread(f.image_path)
    if crop:
        x0, y0, x1, y1 = [int(float(v)) for v in crop.split(",")]
        pad = 12
        img = img[max(0, y0 - pad): min(img.shape[0], y1 + pad), max(0, x0 - pad): min(img.shape[1], x1 + pad)]
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


def _404():
    raise HTTPException(404, "Not found.")
