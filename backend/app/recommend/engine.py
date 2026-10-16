"""Configurable recommendation engine.

Seven scores (0..1), each with plain-language reasons, combined into suggested
actions: Keep / Evolve / Power Up / Appraise / Review for Transfer.
Nothing here modifies or transfers anything.

Honesty notes surfaced to the user:
* League scores use stat product (bulk x attack at the best level under the CP
  cap). Move matchups are not simulated.
* PvE uses PvP move stats as a proxy (the data source has no PvE move table),
  so PvE is always marked provisional.
* With unknown IVs, league scores use species potential only and say so.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .gamedata import CPM, GameData, LEVELS, Species, get_gamedata, stat_product

LEAGUES = {"great": 1500, "ultra": 2500, "master": None}

DEFAULT_CONFIG: Dict[str, Any] = {
    "max_level": 50.0,
    "keep_threshold": 0.75,
    "evolve_threshold": 0.6,
    "power_up_threshold": 0.65,
    "appraise_threshold": 0.5,
    "transfer_max_score": 0.4,
    "transfer_max_rarity": 0.3,
    "weights": {"rarity": 1.0, "pve": 1.0, "great": 1.0, "ultra": 1.0, "master": 1.0},
}


@dataclass
class Score:
    value: float
    reasons: List[str] = field(default_factory=list)
    provisional: bool = False

    def to_json(self) -> dict:
        return {"value": round(self.value, 3), "reasons": self.reasons, "provisional": self.provisional}


# ------------------------------------------------------------------ league math

_IV = np.arange(16)
_A, _D, _S = [x.ravel() for x in np.meshgrid(_IV, _IV, _IV, indexing="ij")]


@lru_cache(maxsize=2048)
def _league_table(atk: int, df: int, sta: int, cap: Optional[int], max_level: float) -> Tuple[np.ndarray, np.ndarray]:
    """Best stat product for each of the 4096 IV combos under the cap, and the sorted SP array for ranking."""
    levels = np.array([lv for lv in LEVELS if lv <= max_level])
    m = np.array([CPM[lv] for lv in levels])[:, None]
    a = (atk + _A)[None, :] * m
    d = (df + _D)[None, :] * m
    s_raw = (sta + _S)[None, :]
    hp = np.maximum(np.floor(s_raw * m), 10)
    cp = np.maximum(np.floor((atk + _A)[None, :] * np.sqrt(df + _D)[None, :] * np.sqrt(sta + _S)[None, :] * m * m / 10.0), 10)
    sp = a * d * hp
    if cap is not None:
        sp = np.where(cp <= cap, sp, -1.0)
    best = sp.max(axis=0)
    return best, np.sort(best)


def iv_league_rank(sp: Species, ivs: Tuple[int, int, int], cap: Optional[int], max_level: float) -> Tuple[int, float]:
    best, sorted_sp = _league_table(sp.atk, sp.df, sp.sta, cap, max_level)
    idx = ivs[0] * 256 + ivs[1] * 16 + ivs[2]
    val = best[idx]
    rank = int(len(sorted_sp) - bisect_left(sorted_sp.tolist(), val))  # 1 = best
    return rank, float(val / sorted_sp[-1]) if sorted_sp[-1] > 0 else 0.0


@lru_cache(maxsize=1)
def _species_potentials() -> Dict[str, Dict[str, float]]:
    """Percentile of each species' representative stat product per league among released species."""
    gd = get_gamedata()
    raw: Dict[str, Dict[str, float]] = {}
    for s in gd.species.values():
        if not s.released:
            continue
        entry = {}
        for league, cap in LEAGUES.items():
            if cap is None:
                entry[league] = stat_product(s.base, (15, 15, 15), 50.0)
            else:
                entry[league] = _rep_sp(s, cap)
        entry["pve"] = _pve_raw(gd, s)
        raw[s.species_id] = entry
    out: Dict[str, Dict[str, float]] = {k: {} for k in raw}
    for key in ("great", "ultra", "master", "pve"):
        vals = sorted(v[key] for v in raw.values())
        for sid, v in raw.items():
            out[sid][key] = bisect_left(vals, v[key]) / max(1, len(vals) - 1)
    return out


def _rep_sp(s: Species, cap: int) -> float:
    """Representative SP at the cap with typical good-for-league IVs (0/15/15 approximates a top rank)."""
    for ivs in ((0, 15, 15), (15, 15, 15)):
        lv = _max_level_under(s, ivs, cap, 50.0)
        if lv is not None:
            return stat_product(s.base, ivs, lv)
    return 0.0


def _max_level_under(s: Species, ivs, cap: int, max_level: float) -> Optional[float]:
    from .gamedata import calc_cp

    best = None
    for lv in LEVELS:
        if lv > max_level:
            break
        if calc_cp(s.base, ivs, lv) <= cap:
            best = lv
    return best


def _pve_raw(gd: GameData, s: Species) -> float:
    fast = [gd.moves[m] for m in s.fast_moves if m in gd.moves]
    charged = [gd.moves[m] for m in s.charged_moves if m in gd.moves]
    if not fast or not charged:
        return 0.0
    best = 0.0
    for f in fast:
        stab_f = 1.2 if f.type in s.types else 1.0
        for c in charged:
            if c.energy <= 0:
                continue
            stab_c = 1.2 if c.type in s.types else 1.0
            per_turn = f.power * stab_f / f.turns + (c.power * stab_c / c.energy) * (f.energy_gain / f.turns)
            best = max(best, per_turn)
    return best * (s.atk + 15) * CPM[40.0]


# ------------------------------------------------------------------ main entry

def _cfg(overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(overrides or {})}
    cfg["weights"] = {**DEFAULT_CONFIG["weights"], **((overrides or {}).get("weights") or {})}
    return cfg


def recommend(p: Dict[str, Any], collection: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`p` and `collection` items are plain dicts of Pokémon fields."""
    cfg = _cfg(config)
    gd = get_gamedata()
    sp = gd.get(p.get("species_id"))
    stale = gd.is_stale()
    if sp is None:
        return {"scores": {}, "actions": [], "provisional": True, "notes": ["Unknown species; no recommendation."]}
    pots = _species_potentials().get(sp.species_id, {"great": 0, "ultra": 0, "master": 0, "pve": 0})
    ivs = None
    if all(p.get(k) is not None for k in ("iv_atk", "iv_def", "iv_sta")):
        ivs = (p["iv_atk"], p["iv_def"], p["iv_sta"])
    same_species = [c for c in collection if c.get("species_id") == sp.species_id]
    scores: Dict[str, Score] = {}

    # 1. Rarity
    r, why = 0.05, []
    if sp.is_legendary or sp.is_mythical or sp.is_ultra_beast:
        r += 0.5
        why.append("Legendary/Mythical/Ultra Beast species.")
    if sp.is_regional_form or sp.form:
        r += 0.15
        why.append(f"Special form: {sp.form}.") if sp.form else None
    for flag, bonus, text in (("shiny", 0.35, "Shiny."), ("lucky", 0.15, "Lucky (reduced power-up cost)."),
                              ("shadow", 0.1, "Shadow."), ("purified", 0.05, "Purified.")):
        if p.get(flag):
            r += bonus
            why.append(text)
    if len(same_species) <= 1:
        r += 0.1
        why.append("Only one of this species in your confirmed collection.")
    else:
        why.append(f"You have {len(same_species)} of this species.")
    if p.get("shiny") is None:
        why.append("Shiny status unknown (not detected from video).")
    scores["rarity"] = Score(min(1.0, r), why)

    # 2. PvE
    pve = pots.get("pve", 0.0)
    pve_why = [f"Species PvE potential in the top {100 - int(pve * 100)}% (attack × best moveset proxy)."]
    if p.get("shadow"):
        pve = min(1.0, pve * 1.15)
        pve_why.append("Shadow attack bonus.")
    if ivs:
        pve *= 0.85 + 0.15 * ivs[0] / 15
        pve_why.append(f"Attack IV {ivs[0]}/15 considered.")
    pve_why.append("Provisional: PvE estimate uses PvP move stats as a proxy.")
    scores["pve"] = Score(pve, pve_why, provisional=True)

    # 3-5. Leagues
    for league, cap in LEAGUES.items():
        pot = pots.get(league, 0.0)
        lw = [f"Species {league.title()} League stat-product potential: top {100 - int(pot * 100)}%."]
        val = pot
        if ivs:
            rank, frac = iv_league_rank(sp, ivs, cap, cfg["max_level"])
            iv_q = max(0.0, 1.0 - (rank - 1) / 4095.0)
            val = pot * (0.55 + 0.45 * iv_q)
            lw.append(f"IV rank {rank}/4096 ({frac * 100:.1f}% of the best stat product).")
        else:
            lw.append("IVs unknown: species potential only.")
        if cap is not None and p.get("cp") is not None and p["cp"] > cap:
            val *= 0.2
            lw.append(f"Already above the {cap} CP cap.")
        lw.append("Moves and matchups not simulated.")
        scores[league] = Score(val, lw, provisional=stale)

    # 6. Evolution priority
    evo_ids = [e for e in sp.evolutions if gd.get(e)]
    if evo_ids:
        best_evo, best_val = None, 0.0
        for e in evo_ids:
            ep = _species_potentials().get(e, {})
            v = max(ep.get("pve", 0), ep.get("great", 0), ep.get("ultra", 0), ep.get("master", 0))
            if v > best_val:
                best_evo, best_val = e, v
        ew = []
        val = best_val
        cur_best = max(scores[k].value for k in ("pve", "great", "ultra", "master"))
        if best_val > cur_best:
            ew.append(f"Evolving to {gd.get(best_evo).display_name} raises its best-use potential.")
        owned_evo = any(c.get("species_id") in evo_ids for c in collection)
        if not owned_evo:
            val = min(1.0, val + 0.2)
            ew.append("New collection entry: you have no confirmed evolutions of this family.")
        if p.get("cp") is not None and p["cp"] > 1500:
            ew.append("Check the evolved CP against league caps before evolving.")
        ew.append("Candy cost not included in game data; check in game.")
        scores["evolution"] = Score(val, ew, provisional=stale)
    else:
        scores["evolution"] = Score(0.0, ["No further evolutions."])

    # 7. Stardust priority
    use = max(scores[k].value for k in ("pve", "great", "ultra", "master"))
    dw = [f"Best use-case score {use:.2f}."]
    dust = use
    if not ivs:
        dust = min(dust, 0.6)
        dw.append("IVs not verified; appraise before investing.")
    if p.get("lucky"):
        dust = min(1.0, dust * 1.2)
        dw.append("Lucky: power-ups cost half the stardust.")
    if p.get("shadow"):
        dw.append("Shadow: power-ups cost 20% more.")
    if p.get("purified"):
        dust = min(1.0, dust * 1.05)
        dw.append("Purified: power-ups are discounted.")
    scores["stardust"] = Score(dust, dw, provisional=stale or scores["pve"].provisional)

    # Actions
    actions: List[Dict[str, Any]] = []
    rarity = scores["rarity"].value
    top = max(scores[k].value for k in ("pve", "great", "ultra", "master"))
    protected = bool(p.get("shiny") or p.get("lucky") or p.get("favorite") or sp.is_legendary or sp.is_mythical)
    if protected or rarity >= 0.5 or top >= cfg["keep_threshold"]:
        actions.append({"action": "Keep", "reasons": [r for r in scores["rarity"].reasons[:2]] +
                        ([f"High use-case score ({top:.2f})."] if top >= cfg["keep_threshold"] else [])})
    if scores["evolution"].value >= cfg["evolve_threshold"]:
        actions.append({"action": "Evolve", "reasons": scores["evolution"].reasons})
    if ivs and scores["stardust"].value >= cfg["power_up_threshold"]:
        actions.append({"action": "Power Up", "reasons": scores["stardust"].reasons})
    if not ivs and top >= cfg["appraise_threshold"]:
        actions.append({"action": "Appraise", "reasons": ["Species has strong potential; verified IVs would decide between investing and transferring."]})
    if not actions and not protected and rarity <= cfg["transfer_max_rarity"] and top <= cfg["transfer_max_score"]:
        better = [c for c in same_species if c.get("id") != p.get("id")]
        reasons = [f"Low scores across all uses (best {top:.2f}) and not rare."]
        if better:
            reasons.append(f"You have {len(better)} other {sp.display_name}.")
        if p.get("shiny") is None:
            reasons.append("Shiny status was not detected; check in game before transferring.")
        reasons.append("PokéVault never transfers Pokémon; this is only a suggestion to review.")
        actions.append({"action": "Review for Transfer", "reasons": reasons})
    if not actions:
        actions.append({"action": "Keep", "reasons": ["No strong signal either way; keep until more evidence (e.g. appraisal)."]})

    return {
        "scores": {k: v.to_json() for k, v in scores.items()},
        "actions": actions,
        "provisional": stale or any(v.provisional for v in scores.values()),
        "gamedata": {"source": gd.source, "timestamp": gd.timestamp, "stale": stale},
    }
