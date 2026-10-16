"""Persist grouped observations and turn reviewed groups into collection Pokémon."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Correction, ObservationGroup, Pokemon, PokemonEvidence, utcnow
from ..recommend.gamedata import get_gamedata
from ..validation import check_pokemon_invariants, clean_pokemon_input
from .identity import find_candidates

EDITABLE_FIELDS = ("species_id", "form", "nickname", "cp", "hp", "shiny", "shadow", "purified", "lucky", "favorite",
                   "iv_atk", "iv_def", "iv_sta", "notes")
KEY_FIELDS = ("species_id", "cp")
AUTOMATIC = ("auto", "auto-link")  # resolved by the scanner, not checked by the owner
FORM_CONFIDENCE_FOR_LINK = 0.75


def pokemon_as_dict(p: Pokemon) -> Dict[str, Any]:
    return {c: getattr(p, c) for c in ("id", "species_id", "dex", "form", "nickname", "cp", "hp", "iv_atk", "iv_def",
                                        "iv_sta", "shiny", "shadow", "purified", "lucky", "favorite", "weight_kg",
                                        "height_m")}


def decide_status(g: ObservationGroup) -> str:
    if g.status in ("confirmed", "rejected"):
        return g.status
    attrs = g.attributes or {}
    if g.review_reasons or g.candidate_pokemon_ids:
        return "needs_review"
    for f in KEY_FIELDS:
        a = attrs.get(f)
        if not a or a.get("value") is None or a.get("confidence", 0) < settings.ready_confidence:
            return "needs_review"
    return "ready"


def refresh_candidates(db: Session, g: ObservationGroup) -> None:
    if g.status in ("confirmed", "rejected"):
        return
    existing = db.scalars(select(Pokemon).where(Pokemon.is_demo.is_(False))).all()
    cands = find_candidates(g.attributes or {}, [pokemon_as_dict(p) for p in existing])
    g.candidate_pokemon_ids = [{"pokemon_id": c.pokemon_id, "score": c.score, "reasons": c.reasons} for c in cands]


def _attr_value(attrs: Dict[str, dict], f: str):
    a = attrs.get(f)
    return None if not a else a.get("value")


def _log(db: Session, target: str, target_id: str, field: str, old, new) -> None:
    if old != new:
        db.add(Correction(target=target, target_id=str(target_id), field=field, old=old, new=new))


def apply_group_edits(db: Session, g: ObservationGroup, edits: Dict[str, Any]) -> None:
    """User corrections on a pending group. Edited values get confidence 1.0 and method 'user'."""
    gd = get_gamedata()
    cleaned = clean_pokemon_input(edits)  # same rules as every other write path; raises InvalidInput
    attrs = dict(g.attributes or {})
    for f, v in cleaned.items():
        if f == "notes":
            continue
        old = _attr_value(attrs, f)
        _log(db, "group", str(g.id), f, old, v)
        attrs[f] = {"value": v, "confidence": 1.0, "method": "user", "frame_id": None}
        if f == "species_id" and v:
            sp = gd.get(v)
            attrs["dex"] = {"value": sp.dex, "confidence": 1.0, "method": "user", "frame_id": None}
            attrs["form"] = {"value": sp.form, "confidence": 1.0, "method": "user", "frame_id": None}
    g.attributes = attrs
    # Edits resolve reading problems, but duplicate/identity questions still need an explicit decision.
    g.review_reasons = [r for r in (g.review_reasons or []) if "duplicate" in r.lower() or "seen earlier" in r]


def confirm_group(db: Session, g: ObservationGroup, link_to: Optional[str] = None, via: str = "review",
                  edits: Optional[Dict[str, Any]] = None) -> Pokemon:
    if edits:
        apply_group_edits(db, g, edits)
    attrs = g.attributes or {}
    species_id = _attr_value(attrs, "species_id")
    if not species_id:
        raise ValueError("Species must be set before confirming.")
    gd = get_gamedata()
    sp = gd.get(species_id)
    if sp is None:
        raise ValueError(f"Unknown species_id {species_id!r}")

    if link_to:
        p = db.get(Pokemon, link_to)
        if p is None:
            raise ValueError("Pokémon to link not found.")
    else:
        p = Pokemon(species_id=species_id, dex=sp.dex, is_demo=False)
        db.add(p)

    provenance = dict(p.provenance or {})
    def take(field: str, column: Optional[str] = None):
        column = column or field
        a = attrs.get(field)
        if a is None or a.get("value") is None:
            return
        cur = getattr(p, column)
        prev = provenance.get(column)
        # Never let a weaker reading overwrite a stronger (or user-entered) one when linking.
        if cur is not None and prev and prev.get("confidence", 0) > a.get("confidence", 0) and a.get("method") != "user":
            return
        setattr(p, column, a["value"])
        provenance[column] = {"confidence": a.get("confidence"), "method": a.get("method"), "frame_id": a.get("frame_id"),
                              "scan_id": g.scan_id, "group_id": g.id}

    take("species_id")
    p.dex = sp.dex
    p.form = sp.form
    provenance["form"] = provenance.get("species_id")
    for f in ("nickname", "cp", "hp", "shiny", "shadow", "purified", "lucky", "favorite", "weight_kg", "height_m"):
        take(f)
    ivs = attrs.get("ivs")
    if ivs and ivs.get("value") is not None:
        p.iv_atk, p.iv_def, p.iv_sta = ivs["value"]
        p.iv_status = "verified" if ivs.get("method") in ("appraisal:verified", "user") else "range"
        provenance["ivs"] = {k: ivs.get(k) for k in ("confidence", "method", "frame_id")}
    elif attrs.get("iv_candidates") and p.iv_status != "verified":
        p.iv_candidates = attrs["iv_candidates"]["value"]
        p.iv_status = "range"
    for f in ("iv_atk", "iv_def", "iv_sta"):
        if attrs.get(f) and attrs[f].get("method") == "user":
            setattr(p, f, attrs[f]["value"])
            p.iv_status = "verified" if all(getattr(p, x) is not None for x in ("iv_atk", "iv_def", "iv_sta")) else p.iv_status
    if edits and "notes" in edits:
        p.notes = edits["notes"] or ""

    confs = [attrs[f]["confidence"] for f in KEY_FIELDS if attrs.get(f)]
    p.confidence = round(min(confs) if confs else 0.0, 3)
    p.provenance = provenance
    if p.iv_status == "verified" and any(getattr(p, f) is None for f in ("iv_atk", "iv_def", "iv_sta")):
        p.iv_status = "range" if p.iv_candidates else "unknown"
    # Final state must satisfy the same invariants as manual edits (scanner output included).
    check_pokemon_invariants({f: getattr(p, f) for f in ("cp", "hp", "iv_atk", "iv_def", "iv_sta", "shadow",
                                                         "purified", "iv_status")})
    # "auto" means the scanner was confident and the owner opted in; it is not a human check.
    # Never downgrade a Pokémon the owner already checked.
    automatic = via in AUTOMATIC
    if not automatic or not p.user_confirmed:
        p.user_confirmed = not automatic
        p.confirmed_via = via
    p.last_seen_at = utcnow()
    db.flush()
    db.add(PokemonEvidence(pokemon_id=p.id, group_id=g.id, scan_id=g.scan_id, frame_id=g.best_frame_id,
                           kind=via if via in ("auto-link", "link") else "observation"))
    g.pokemon_id = p.id
    g.status = "confirmed"
    g.link_method = via
    g.candidate_pokemon_ids = []
    return p


def reject_group(db: Session, g: ObservationGroup) -> None:
    _log(db, "group", str(g.id), "status", g.status, "rejected")
    g.status = "rejected"


def _scope(stmt, attempt_id: Optional[str]):
    """Staged results of one attempt, or (default) the published results the user sees."""
    if attempt_id is not None:
        return stmt.where(ObservationGroup.attempt_id == attempt_id)
    return stmt.where(ObservationGroup.published.is_(True))


def bulk_confirm_ready(db: Session, scan_id: int, via: str = "bulk", attempt_id: Optional[str] = None) -> List[str]:
    stmt = select(ObservationGroup).where(ObservationGroup.scan_id == scan_id, ObservationGroup.status == "ready")
    return [confirm_group(db, g, via=via).id for g in db.scalars(_scope(stmt, attempt_id)).all()]

def _strong_value(attrs: Dict[str, dict], f: str, min_conf: float):
    a = attrs.get(f)
    if not a or a.get("value") is None or a.get("confidence", 0) < min_conf:
        return None
    return a["value"]


def _positive_evidence(attrs: Dict[str, dict], p: Pokemon) -> List[str]:
    """Matches beyond species+CP that point to the SAME individual. Both sides must actually have the value."""
    found: List[str] = []
    hp = _strong_value(attrs, "hp", 0.5)
    if hp is not None and p.hp is not None and hp == p.hp:
        found.append("HP")
    nick = _strong_value(attrs, "nickname", 0.5)
    if nick and p.nickname and nick.strip().lower() == p.nickname.strip().lower():
        found.append("nickname")
    w, h = _strong_value(attrs, "weight_kg", 0.5), _strong_value(attrs, "height_m", 0.5)
    if None not in (w, h, p.weight_kg, p.height_m) and abs(w - p.weight_kg) <= 0.005 and abs(h - p.height_m) <= 0.005:
        found.append("weight+height")
    ivs = _strong_value(attrs, "ivs", 0.5)
    if ivs is not None and p.iv_status == "verified" and list(ivs) == [p.iv_atk, p.iv_def, p.iv_sta]:
        found.append("IVs")
    return found


def linkable_resightings(db: Session, scan_id: int, attempt_id: Optional[str] = None) -> List[tuple]:
    """(group, pokemon_id) pairs that may be linked: unambiguous AND corroborated.

    All of these must hold (anything else stays in Review):
      * the group's only open question is the identity match (no other review reasons);
      * species/form and CP were read with high confidence, and the form is not in doubt;
      * exactly ONE existing Pokémon is compatible (identity.compare already rejects any conflicting field);
      * nicknames are identical, or both have none;
      * at least one POSITIVE match beyond species and CP: HP, custom nickname, weight+height, or verified IVs.
        Missing data is never treated as agreement;
      * no other group in this scan points at the same Pokémon.
    """
    groups = db.scalars(_scope(select(ObservationGroup).where(ObservationGroup.scan_id == scan_id,
                                                              ObservationGroup.status == "needs_review"),
                               attempt_id)).all()
    claims: Dict[str, int] = {}
    for g in groups:
        for c in g.candidate_pokemon_ids or []:
            claims[c["pokemon_id"]] = claims.get(c["pokemon_id"], 0) + 1
    already = {pid for (pid,) in db.execute(_scope(select(ObservationGroup.pokemon_id).where(
        ObservationGroup.scan_id == scan_id, ObservationGroup.pokemon_id.isnot(None)), attempt_id))}

    out: List[tuple] = []
    for g in groups:
        cands = g.candidate_pokemon_ids or []
        if len(cands) != 1 or g.review_reasons:
            continue
        pid = cands[0]["pokemon_id"]
        if claims.get(pid, 0) != 1 or pid in already:
            continue
        attrs = g.attributes or {}
        if _strong_value(attrs, "species_id", settings.ready_confidence) is None:
            continue
        if _strong_value(attrs, "cp", settings.ready_confidence) is None:
            continue
        form = attrs.get("form")
        if form is not None and form.get("confidence", 0) < FORM_CONFIDENCE_FOR_LINK:
            continue
        p = db.get(Pokemon, pid)
        if p is None or p.species_id != attrs["species_id"]["value"] or p.cp != attrs["cp"]["value"]:
            continue
        nick = _strong_value(attrs, "nickname", 0.5)
        if (nick or "").strip().lower() != (p.nickname or "").strip().lower():
            continue
        hp = _strong_value(attrs, "hp", 0.5)
        if hp is not None and p.hp is not None and hp != p.hp:
            continue
        w = _strong_value(attrs, "weight_kg", 0.5)
        if w is not None and p.weight_kg is not None and abs(w - p.weight_kg) > 0.005:
            continue
        if not _positive_evidence(attrs, p):
            continue  # species + CP alone is never enough
        out.append((g, pid))
        already.add(pid)
    return out


def auto_link_resightings(db: Session, scan_id: int, attempt_id: Optional[str] = None,
                          via: str = "auto-link") -> List[str]:
    """Link every unambiguous, corroborated re-sighting (see linkable_resightings).

    via="auto-link" when the scanner does it (auto-confirm setting); "link" when the owner clicks
    "Link all matches", which counts as the owner's decision.
    """
    linked = []
    for g, pid in linkable_resightings(db, scan_id, attempt_id):
        confirm_group(db, g, link_to=pid, via=via)
        linked.append(pid)
    return linked
