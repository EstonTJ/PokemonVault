"""CSV / JSON exports. Unknown values are exported as empty cells / null, never as 'false'."""
from __future__ import annotations

import csv
import io
import json
from typing import Iterable, List

from ..models import Pokemon
from ..recommend.gamedata import get_gamedata

COLUMNS = ["id", "dex", "species", "form", "nickname", "cp", "hp", "iv_atk", "iv_def", "iv_sta", "iv_status", "weight_kg", "height_m", "shiny",
           "shadow", "purified", "lucky", "favorite", "confidence", "user_confirmed", "confirmed_via", "first_seen_at",
           "last_seen_at", "notes", "is_demo"]


def _tri(v):
    return "" if v is None else ("yes" if v else "no")


def _row(p: Pokemon) -> dict:
    sp = get_gamedata().get(p.species_id)
    return {
        "id": p.id, "dex": p.dex, "species": sp.base_name if sp else p.species_id, "form": p.form or "",
        "nickname": p.nickname or "", "cp": p.cp if p.cp is not None else "", "hp": p.hp if p.hp is not None else "",
        "iv_atk": "" if p.iv_atk is None else p.iv_atk, "iv_def": "" if p.iv_def is None else p.iv_def,
        "iv_sta": "" if p.iv_sta is None else p.iv_sta, "iv_status": p.iv_status,
        "weight_kg": "" if p.weight_kg is None else p.weight_kg, "height_m": "" if p.height_m is None else p.height_m,
        "shiny": _tri(p.shiny), "shadow": _tri(p.shadow), "purified": _tri(p.purified), "lucky": _tri(p.lucky),
        "favorite": _tri(p.favorite), "confidence": p.confidence, "user_confirmed": "yes" if p.user_confirmed else "no",
        "confirmed_via": p.confirmed_via, "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else "",
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else "", "notes": p.notes or "",
        "is_demo": "yes" if p.is_demo else "no",
    }


def _safe(v):
    # Neutralise spreadsheet formula injection in user-controlled text.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def collection_csv(pokes: Iterable[Pokemon]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for p in pokes:
        w.writerow({k: _safe(v) for k, v in _row(p).items()})
    return buf.getvalue()


def pokemon_record(p: Pokemon) -> dict:
    return {
        "id": p.id, "species_id": p.species_id, "dex": p.dex, "form": p.form, "nickname": p.nickname, "cp": p.cp, "hp": p.hp,
        "ivs": {"atk": p.iv_atk, "def": p.iv_def, "sta": p.iv_sta, "status": p.iv_status, "candidates": p.iv_candidates},
        "weight_kg": p.weight_kg, "height_m": p.height_m,
        "shiny": p.shiny, "shadow": p.shadow, "purified": p.purified, "lucky": p.lucky, "favorite": p.favorite,
        "confidence": p.confidence, "provenance": p.provenance, "user_confirmed": p.user_confirmed,
        "confirmed_via": p.confirmed_via, "notes": p.notes, "is_demo": p.is_demo,
        "first_seen_at": p.first_seen_at.isoformat() if p.first_seen_at else None,
        "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
    }


def collection_json(pokes: List[Pokemon]) -> str:
    gd = get_gamedata()
    return json.dumps({"format": "pokevault.collection.v1", "gamedata": gd.timestamp,
                       "pokemon": [pokemon_record(p) for p in pokes]}, indent=2)
