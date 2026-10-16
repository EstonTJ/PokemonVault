"""Collection CRUD, search/filter/sort, recommendations, dashboard and exports."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import Owner
from ..catalog.importer import EDITABLE_FIELDS
from ..db import get_db
from ..export.exporters import collection_csv, collection_json
from ..models import AppSetting, Correction, ObservationGroup, Pokemon, PokemonEvidence, Scan
from ..recommend.engine import recommend
from ..recommend.gamedata import get_gamedata
from .serialize import iso, pokemon_json, pokemon_plain, scan_json

router = APIRouter(prefix="/api", dependencies=[Owner])

SORTS = {"cp": Pokemon.cp, "dex": Pokemon.dex, "recent": Pokemon.created_at, "name": Pokemon.species_id,
         "confidence": Pokemon.confidence}
TRI_FLAGS = ("shiny", "shadow", "purified", "lucky", "favorite")


def _rec_config(db: Session) -> Dict[str, Any]:
    s = db.get(AppSetting, "recommendation_config")
    return (s.value if s else None) or {}


def _query(db: Session, q: Optional[str], filters: Dict[str, Optional[str]], sort: str, desc: bool, include_demo: bool):
    stmt = select(Pokemon)
    if not include_demo:
        stmt = stmt.where(Pokemon.is_demo.is_(False))
    if q:
        gd = get_gamedata()
        ql = q.lower()
        ids = [s.species_id for s in gd.species.values() if ql in s.display_name.lower()]
        stmt = stmt.where(or_(Pokemon.species_id.in_(ids), Pokemon.nickname.ilike(f"%{q}%"), Pokemon.notes.ilike(f"%{q}%")))
    for f in TRI_FLAGS:
        v = filters.get(f)
        if v == "yes":
            stmt = stmt.where(getattr(Pokemon, f).is_(True))
        elif v == "no":
            stmt = stmt.where(getattr(Pokemon, f).is_(False))
        elif v == "unknown":
            stmt = stmt.where(getattr(Pokemon, f).is_(None))
    if filters.get("checked") == "no":
        stmt = stmt.where(Pokemon.user_confirmed.is_(False), Pokemon.is_demo.is_(False))
    if filters.get("iv_status"):
        stmt = stmt.where(Pokemon.iv_status == filters["iv_status"])
    if filters.get("min_cp"):
        stmt = stmt.where(Pokemon.cp >= int(filters["min_cp"]))
    col = SORTS.get(sort, Pokemon.created_at)
    stmt = stmt.order_by(col.desc() if desc else col.asc(), Pokemon.id)
    return db.scalars(stmt).all()


@router.get("/pokemon")
def list_pokemon(q: Optional[str] = None, shiny: Optional[str] = None, shadow: Optional[str] = None,
                 purified: Optional[str] = None, lucky: Optional[str] = None, favorite: Optional[str] = None,
                 iv_status: Optional[str] = None, min_cp: Optional[int] = None, special: Optional[str] = None,
                 checked: Optional[str] = None,
                 sort: str = "recent", desc: bool = True, include_demo: bool = True, db: Session = Depends(get_db)):
    filters = dict(shiny=shiny, shadow=shadow, purified=purified, lucky=lucky, favorite=favorite, iv_status=iv_status,
                   min_cp=str(min_cp) if min_cp else None, checked=checked)
    items = [pokemon_json(p) for p in _query(db, q, filters, sort, desc, include_demo)]
    if special == "yes":
        items = [p for p in items if p["legendary"] or p["special_form"]]
    if sort == "name":
        items.sort(key=lambda p: p["species_name"], reverse=desc)
    return items


@router.get("/pokemon/{pid}")
def get_pokemon(pid: str, db: Session = Depends(get_db)):
    p = db.get(Pokemon, pid) or _404()
    out = pokemon_json(p)
    coll = [pokemon_plain(x) for x in db.scalars(select(Pokemon).where(Pokemon.is_demo == p.is_demo)).all()]
    out["recommendation"] = recommend(pokemon_plain(p), coll, _rec_config(db))
    ev = db.scalars(select(PokemonEvidence).where(PokemonEvidence.pokemon_id == pid).order_by(PokemonEvidence.id)).all()
    evidence = []
    for e in ev:
        g = db.get(ObservationGroup, e.group_id) if e.group_id else None
        evidence.append({"id": e.id, "kind": e.kind, "scan_id": e.scan_id, "frame_id": e.frame_id, "group_id": e.group_id,
                         "bbox": g.best_bbox if g else None, "created_at": iso(e.created_at)})
    out["evidence"] = evidence
    out["corrections"] = [{"field": c.field, "old": c.old, "new": c.new, "at": iso(c.at)} for c in
                          db.scalars(select(Correction).where(Correction.target == "pokemon", Correction.target_id == pid)
                                     .order_by(Correction.id.desc())).all()]
    return out


@router.patch("/pokemon/{pid}")
def edit_pokemon(pid: str, edits: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    p = db.get(Pokemon, pid) or _404()
    gd = get_gamedata()
    prov = dict(p.provenance or {})
    for f, v in edits.items():
        if f not in EDITABLE_FIELDS:
            raise HTTPException(400, f"Field {f!r} is not editable.")
        if f in ("iv_atk", "iv_def", "iv_sta") and v is not None and not (0 <= int(v) <= 15):
            raise HTTPException(400, "IVs must be 0-15.")
        if f == "cp" and v is not None and not (10 <= int(v) <= 6000):
            raise HTTPException(400, "CP out of range.")
        old = getattr(p, f)
        if f == "species_id":
            sp = gd.get(v)
            if sp is None:
                raise HTTPException(400, "Unknown species.")
            p.dex, p.form = sp.dex, sp.form
        if old != v:
            db.add(Correction(target="pokemon", target_id=pid, field=f, old=old, new=v))
            setattr(p, f, v)
            if f != "notes":
                prov[f] = {"confidence": 1.0, "method": "user"}
    if any(f in edits for f in ("iv_atk", "iv_def", "iv_sta")):
        have = all(getattr(p, f) is not None for f in ("iv_atk", "iv_def", "iv_sta"))
        p.iv_status = "verified" if have else ("range" if p.iv_candidates else "unknown")
    p.provenance = prov
    db.commit()
    return pokemon_json(p)


@router.post("/pokemon/{pid}/mark-checked")
def mark_checked(pid: str, db: Session = Depends(get_db)):
    """Owner reviewed an auto-confirmed Pokémon and agrees with it."""
    p = db.get(Pokemon, pid) or _404()
    if not p.user_confirmed:
        db.add(Correction(target="pokemon", target_id=pid, field="user_confirmed", old=False, new=True))
        p.user_confirmed = True
    db.commit()
    return pokemon_json(p)


@router.post("/pokemon", status_code=201)
def add_manual(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    sp = get_gamedata().get(body.get("species_id"))
    if sp is None:
        raise HTTPException(400, "Unknown species.")
    p = Pokemon(species_id=sp.species_id, dex=sp.dex, form=sp.form, user_confirmed=True, confirmed_via="manual", confidence=1.0)
    for f in EDITABLE_FIELDS:
        if f in body and f != "species_id":
            setattr(p, f, body[f])
    if all(getattr(p, f) is not None for f in ("iv_atk", "iv_def", "iv_sta")):
        p.iv_status = "verified"
    p.provenance = {f: {"confidence": 1.0, "method": "user"} for f in body}
    db.add(p)
    db.commit()
    return pokemon_json(p)


@router.delete("/pokemon/{pid}")
def delete_pokemon(pid: str, db: Session = Depends(get_db)):
    p = db.get(Pokemon, pid) or _404()
    for g in db.scalars(select(ObservationGroup).where(ObservationGroup.pokemon_id == pid)).all():
        g.status, g.pokemon_id = "needs_review", None
        g.review_reasons = list(g.review_reasons or []) + ["Previously confirmed Pokémon was deleted."]
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/species")
def species_search(q: str = Query("", max_length=40), limit: int = 20):
    gd = get_gamedata()
    ql = q.lower().strip()
    out = []
    for s in sorted(gd.species.values(), key=lambda s: (s.dex, s.form or "")):
        if not ql or ql in s.display_name.lower() or ql == str(s.dex):
            out.append({"species_id": s.species_id, "name": s.display_name, "dex": s.dex, "form": s.form, "types": list(s.types)})
            if len(out) >= limit:
                break
    return out


# ------------------------------------------------------------------ recommendations & dashboard

def _all_recs(db: Session, include_demo: bool):
    stmt = select(Pokemon)
    if not include_demo:
        stmt = stmt.where(Pokemon.is_demo.is_(False))
    pokes = db.scalars(stmt).all()
    plain = [pokemon_plain(p) for p in pokes]
    cfg = _rec_config(db)
    return [(p, recommend(pp, [x for x, y in zip(plain, pokes) if y.is_demo == p.is_demo], cfg)) for p, pp in zip(pokes, plain)]


@router.get("/recommendations")
def recommendations(include_demo: bool = True, db: Session = Depends(get_db)):
    buckets: Dict[str, List[dict]] = {"Keep": [], "Evolve": [], "Power Up": [], "Appraise": [], "Review for Transfer": []}
    provisional = False
    for p, r in _all_recs(db, include_demo):
        provisional = provisional or r.get("provisional", False)
        for a in r["actions"]:
            buckets[a["action"]].append({"pokemon": pokemon_json(p), "reasons": a["reasons"], "scores": r["scores"]})
    for k in ("Evolve", "Power Up"):
        key = "evolution" if k == "Evolve" else "stardust"
        buckets[k].sort(key=lambda x: -x["scores"][key]["value"])
    gd = get_gamedata()
    return {"buckets": buckets, "provisional": provisional,
            "gamedata": {"source": gd.source, "timestamp": gd.timestamp, "stale": gd.is_stale()}}


@router.get("/dashboard")
def dashboard(include_demo: bool = True, db: Session = Depends(get_db)):
    stmt = select(Pokemon)
    if not include_demo:
        stmt = stmt.where(Pokemon.is_demo.is_(False))
    pokes = db.scalars(stmt).all()
    js = [pokemon_json(p) for p in pokes]
    pending = db.scalars(select(ObservationGroup).where(ObservationGroup.status.in_(("needs_review", "ready")))).all()
    dup_groups = [g for g in pending if g.candidate_pokemon_ids or any("duplicate" in r.lower() for r in (g.review_reasons or []))]
    recs = _all_recs(db, include_demo)
    evolve = [{"pokemon": pokemon_json(p), "reasons": a["reasons"]} for p, r in recs for a in r["actions"] if a["action"] == "Evolve"]
    power = [{"pokemon": pokemon_json(p), "reasons": a["reasons"]} for p, r in recs for a in r["actions"] if a["action"] == "Power Up"]
    scans = db.scalars(select(Scan).order_by(Scan.id.desc()).limit(8)).all()
    return {
        "total": len(js),
        "unique_species": len({p["dex"] for p in js}),
        "shiny": sum(1 for p in js if p["shiny"]),
        "shadow": sum(1 for p in js if p["shadow"]),
        "lucky": sum(1 for p in js if p["lucky"]),
        "special": [p for p in js if p["legendary"] or p["special_form"] or p["shiny"]][:12],
        "special_count": sum(1 for p in js if p["legendary"] or p["special_form"]),
        "unresolved": len(pending),
        "needs_review": sum(1 for g in pending if g.status == "needs_review"),
        "ready": sum(1 for g in pending if g.status == "ready"),
        "possible_duplicates": len(dup_groups),
        "evolve": evolve[:6], "evolve_count": len(evolve),
        "power_up": power[:6], "power_up_count": len(power),
        "demo_count": sum(1 for p in js if p["is_demo"]),
        "scans": [scan_json(s) for s in scans],
        "unknown_shiny": sum(1 for p in js if p["shiny"] is None),
        "auto_unchecked": sum(1 for p in js if p["confirmed_via"] in ("auto", "auto-link") and not p["user_confirmed"]),
    }


# ------------------------------------------------------------------ export

@router.get("/export/collection.csv")
def export_csv(include_demo: bool = False, db: Session = Depends(get_db)):
    pokes = _query(db, None, {}, "dex", False, include_demo)
    return Response(collection_csv(pokes), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="pokevault-collection.csv"', "Cache-Control": "no-store"})


@router.get("/export/collection.json")
def export_json(include_demo: bool = False, db: Session = Depends(get_db)):
    pokes = _query(db, None, {}, "dex", False, include_demo)
    return Response(collection_json(pokes), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="pokevault-collection.json"', "Cache-Control": "no-store"})


def _404():
    raise HTTPException(404, "Not found.")
