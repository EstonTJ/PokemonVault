from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import Job, ObservationGroup, Pokemon, Scan
from ..recommend.gamedata import get_gamedata


def iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def job_json(j: Optional[Job]) -> Optional[Dict[str, Any]]:
    if j is None:
        return None
    return {"id": j.id, "status": j.status, "progress": round(j.progress, 3), "stage": j.stage, "attempts": j.attempts,
            "max_attempts": j.max_attempts, "error": j.error, "cancel_requested": j.cancel_requested,
            "created_at": iso(j.created_at), "finished_at": iso(j.finished_at)}


def scan_json(s: Scan, job: Optional[Job] = None) -> Dict[str, Any]:
    return {"id": s.id, "created_at": iso(s.created_at), "original_filename": s.original_filename, "size_bytes": s.size_bytes,
            "kind": s.kind, "status": s.status, "keep_video": s.keep_video, "video_stored": bool(s.video_path),
            "video_deleted_at": iso(s.video_deleted_at), "duration_s": s.duration_s, "width": s.width, "height": s.height,
            "frames_retained": s.frames_retained, "is_demo": s.is_demo, "summary": s.summary or {}, "error": s.error,
            "job": job_json(job)}


def species_label(species_id: Optional[str]) -> Optional[str]:
    sp = get_gamedata().get(species_id)
    return sp.display_name if sp else None


def group_json(g: ObservationGroup) -> Dict[str, Any]:
    attrs = g.attributes or {}
    sid = (attrs.get("species_id") or {}).get("value")
    return {"id": g.id, "scan_id": g.scan_id, "track_key": g.track_key, "status": g.status, "attributes": attrs,
            "species_name": species_label(sid), "review_reasons": g.review_reasons or [],
            "candidates": g.candidate_pokemon_ids or [], "pokemon_id": g.pokemon_id,
            "observation_count": g.observation_count, "first_ts_ms": g.first_ts_ms, "last_ts_ms": g.last_ts_ms,
            "best_frame_id": g.best_frame_id, "best_bbox": g.best_bbox, "link_method": g.link_method}


def pokemon_json(p: Pokemon) -> Dict[str, Any]:
    gd = get_gamedata()
    sp = gd.get(p.species_id)
    return {"id": p.id, "species_id": p.species_id, "species_name": sp.display_name if sp else p.species_id, "dex": p.dex,
            "types": list(sp.types) if sp else [], "form": p.form, "nickname": p.nickname, "cp": p.cp, "hp": p.hp,
            "iv_atk": p.iv_atk, "iv_def": p.iv_def, "iv_sta": p.iv_sta, "iv_status": p.iv_status,
            "iv_candidates": p.iv_candidates, "level": p.level, "weight_kg": p.weight_kg, "height_m": p.height_m, "shiny": p.shiny, "shadow": p.shadow, "purified": p.purified,
            "lucky": p.lucky, "favorite": p.favorite, "confidence": p.confidence, "provenance": p.provenance or {},
            "user_confirmed": p.user_confirmed, "confirmed_via": p.confirmed_via, "notes": p.notes, "is_demo": p.is_demo,
            "legendary": bool(sp and (sp.is_legendary or sp.is_mythical or sp.is_ultra_beast)),
            "special_form": bool(sp and sp.form), "first_seen_at": iso(p.first_seen_at), "last_seen_at": iso(p.last_seen_at),
            "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}


def pokemon_plain(p: Pokemon) -> Dict[str, Any]:
    return {c: getattr(p, c) for c in ("id", "species_id", "cp", "hp", "iv_atk", "iv_def", "iv_sta", "shiny", "shadow",
                                        "purified", "lucky", "favorite", "nickname", "weight_kg", "height_m")}
