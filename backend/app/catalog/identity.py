"""Conservative matching of observation groups to existing confirmed Pokémon.

Species + CP is never treated as proof of identity. The outcomes are:
  * "new"         - no existing Pokémon is compatible
  * "candidates"  - one or more compatible Pokémon exist; the user must decide
Automatic linking happens only through a deterministic recording fingerprint
(handled by the importer when the same recording is reprocessed).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

FLAGS = ("shiny", "shadow", "purified", "lucky")


@dataclass
class Candidate:
    pokemon_id: str
    score: float
    reasons: List[str]


def _val(attrs: Dict[str, dict], f: str, min_conf: float = 0.5):
    a = attrs.get(f)
    if not a or a.get("value") is None or a.get("confidence", 0) < min_conf:
        return None
    return a["value"]


def compare(attrs: Dict[str, dict], p: dict) -> Optional[Candidate]:
    """Return a candidate when nothing contradicts. `p` is a plain dict of Pokémon fields."""
    reasons: List[str] = []
    species = _val(attrs, "species_id")
    if species is None or species != p.get("species_id"):
        return None
    cp = _val(attrs, "cp")
    if cp is None or cp != p.get("cp"):
        return None
    score = 0.4
    reasons.append("Same species and CP (not sufficient on its own).")
    hp = _val(attrs, "hp")
    if hp is not None and p.get("hp") is not None:
        if hp != p["hp"]:
            return None
        score += 0.15
        reasons.append("HP matches.")
    nick = _val(attrs, "nickname")
    if nick is not None and p.get("nickname"):
        if nick.strip().lower() != str(p["nickname"]).strip().lower():
            return None
        score += 0.25
        reasons.append("Custom nickname matches.")
    fp_matches = 0
    for f in ("weight_kg", "height_m"):
        v = _val(attrs, f)
        if v is not None and p.get(f) is not None:
            if abs(float(v) - float(p[f])) > 0.005:
                return None  # different individual (weight/height don't change with power-ups)
            fp_matches += 1
    if fp_matches == 2:
        score += 0.35
        reasons.append("Weight and height match exactly.")
    for f in FLAGS:
        v = _val(attrs, f)
        if v is not None and p.get(f) is not None:
            if v != p[f]:
                return None
            score += 0.05
    ivs = _val(attrs, "ivs")
    if ivs is not None and p.get("iv_atk") is not None:
        if list(ivs) != [p["iv_atk"], p["iv_def"], p["iv_sta"]]:
            return None
        score += 0.2
        reasons.append("Verified IVs match.")
    return Candidate(p["id"], round(min(score, 0.99), 3), reasons)


def find_candidates(attrs: Dict[str, dict], existing: List[dict]) -> List[Candidate]:
    out = [c for c in (compare(attrs, p) for p in existing) if c is not None]
    out.sort(key=lambda c: -c.score)
    return out
