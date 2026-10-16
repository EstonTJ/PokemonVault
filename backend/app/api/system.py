"""Auth, health, settings, privacy (export-all / delete-all) and demo fixtures."""
from __future__ import annotations

import json
import shutil
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import auth
from ..auth import Owner
from ..config import settings
from ..db import get_db
from ..demo import load_demo, clear_demo
from ..export.exporters import pokemon_record
from ..models import (AppSetting, Appraisal, Correction, Frame, Job, Observation, ObservationGroup, Pokemon,
                      PokemonEvidence, Scan)
from ..ocr.engine import get_engine
from ..recommend.engine import DEFAULT_CONFIG
from ..recommend.gamedata import get_gamedata
from .serialize import group_json, iso, scan_json

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Owner])


@public.get("/health")
def health():
    return {"ok": True}


@public.post("/auth/login")
def login(request: Request, response: Response, body: Dict[str, str] = Body(...)):
    auth.login(request, response, str(body.get("password", "")))
    return {"authenticated": True}


@public.post("/auth/logout", dependencies=[Depends(auth.require_csrf_header)])
def logout(response: Response):
    auth.logout(response)
    return {"authenticated": False}


@public.get("/auth/me")
def me(request: Request):
    return {"authenticated": auth.is_authenticated(request)}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    stored = {s.key: s.value for s in db.scalars(select(AppSetting)).all()}
    gd = get_gamedata()
    try:
        ocr_name = get_engine().name
    except RuntimeError as exc:
        ocr_name = f"unavailable: {exc}"
    return {
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_duration_s": settings.max_duration_s,
                   "max_uploads_per_hour": settings.max_uploads_per_hour, "max_keyframes": settings.max_keyframes},
        "recommendation_config": stored.get("recommendation_config") or DEFAULT_CONFIG,
        "external_ai_opt_in": bool(stored.get("external_ai_opt_in", False)),
        "default_keep_video": bool(stored.get("default_keep_video", False)),
        "auto_confirm_ready": bool(stored.get("auto_confirm_ready", False)),
        "upload_key": auth.upload_key_info(db),
        "ocr_engine": ocr_name,
        "gamedata": {"source": gd.source, "timestamp": gd.timestamp, "stale": gd.is_stale(), "species": len(gd.species)},
    }


@router.put("/settings")
def put_settings(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    allowed = {"recommendation_config", "default_keep_video", "auto_confirm_ready"}
    for k, v in body.items():
        if k == "external_ai_opt_in":
            # No external AI integration exists; refuse to silently pretend otherwise.
            raise HTTPException(400, "No external AI service is integrated, so there is nothing to opt in to.")
        if k not in allowed:
            raise HTTPException(400, f"Unknown setting {k!r}.")
        row = db.get(AppSetting, k) or AppSetting(key=k)
        row.value = v
        db.merge(row)
    db.commit()
    return get_settings(db)


@router.post("/settings/upload-key")
def create_upload_key(db: Session = Depends(get_db)):
    """Create (or rotate) the upload-only key. The raw key is returned once and never stored."""
    key = auth.create_upload_key(db)
    return {"key": key, **auth.upload_key_info(db)}


@router.delete("/settings/upload-key")
def revoke_upload_key(db: Session = Depends(get_db)):
    auth.revoke_upload_key(db)
    return auth.upload_key_info(db)


@router.get("/privacy/export")
def export_all(db: Session = Depends(get_db)):
    data = {
        "format": "pokevault.export-all.v1",
        "note": "Contains all database records. Images and videos are not included.",
        "pokemon": [pokemon_record(p) for p in db.scalars(select(Pokemon)).all()],
        "scans": [scan_json(s) for s in db.scalars(select(Scan)).all()],
        "observation_groups": [group_json(g) for g in db.scalars(select(ObservationGroup).where(ObservationGroup.published.is_(True))).all()],
        "appraisals": [{"id": a.id, "scan_id": a.scan_id, "group_id": a.group_id, "pokemon_id": a.pokemon_id, "bars": a.bars,
                        "status": a.status, "confidence": a.confidence, "iv_candidates": a.iv_candidates}
                       for a in db.scalars(select(Appraisal)).all()],
        "corrections": [{"target": c.target, "target_id": c.target_id, "field": c.field, "old": c.old, "new": c.new,
                         "at": iso(c.at)} for c in db.scalars(select(Correction)).all()],
        "settings": {s.key: s.value for s in db.scalars(select(AppSetting)).all() if s.key != auth.UPLOAD_KEY_SETTING},
    }
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="pokevault-export-all.json"', "Cache-Control": "no-store"})


@router.post("/privacy/delete-all")
def delete_all(body: Dict[str, str] = Body(...), db: Session = Depends(get_db)):
    if body.get("confirm") != "DELETE ALL":
        raise HTTPException(400, "Type DELETE ALL to confirm.")
    if db.query(Job).filter(Job.status == "running").count():
        raise HTTPException(409, "Cancel running processing first.")
    for model in (PokemonEvidence, Appraisal, Observation, Correction, Job):
        db.execute(delete(model))
    db.execute(delete(ObservationGroup))
    db.execute(delete(Frame))
    db.execute(delete(Pokemon))
    db.execute(delete(Scan))
    db.execute(delete(AppSetting))
    db.commit()
    shutil.rmtree(settings.upload_dir, ignore_errors=True)
    shutil.rmtree(settings.frame_dir, ignore_errors=True)
    settings.ensure_dirs()
    return {"ok": True}


@router.post("/demo/load")
def demo_load(db: Session = Depends(get_db)):
    n = load_demo(db)
    return {"loaded": n}


@router.delete("/demo")
def demo_clear(db: Session = Depends(get_db)):
    n = clear_demo(db)
    return {"removed": n}
