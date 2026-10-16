"""Versioned Pokémon GO reference data and core stat math.

Source: PvPoke `gamemaster.json` (MIT licence). The file's own timestamp is
recorded so rankings can be labelled provisional when the data is stale.

Shadow and Mega entries are folded away: shadow is a per-Pokémon flag, and
Mega is a temporary battle state rather than a collectible form.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import settings

# CP multipliers for whole levels 1..51. Half levels are derived below.
_CPM_WHOLE = [
    0.094, 0.16639787, 0.21573247, 0.25572005, 0.29024988, 0.3210876, 0.34921268, 0.3752356, 0.39956728, 0.4225,
    0.44310755, 0.4627984, 0.48168495, 0.49985844, 0.51739395, 0.53435433, 0.5507927, 0.5667545, 0.5822789, 0.5974,
    0.6121573, 0.6265671, 0.64065295, 0.65443563, 0.667934, 0.6811649, 0.69414365, 0.7068842, 0.7193991, 0.7317,
    0.7377695, 0.74378943, 0.74976104, 0.7556855, 0.76156384, 0.76739717, 0.7731865, 0.77893275, 0.784637, 0.7903,
    0.79530001, 0.8003, 0.8053, 0.81029999, 0.81529999, 0.82029999, 0.82529999, 0.83029999, 0.83529999, 0.84029999,
    0.84529999,
]


def _build_cpm() -> Dict[float, float]:
    table: Dict[float, float] = {}
    for i, v in enumerate(_CPM_WHOLE):
        lvl = float(i + 1)
        table[lvl] = v
        if i + 1 < len(_CPM_WHOLE):
            nxt = _CPM_WHOLE[i + 1]
            table[lvl + 0.5] = math.sqrt((v * v + nxt * nxt) / 2.0)
    return table


CPM: Dict[float, float] = _build_cpm()
LEVELS: List[float] = sorted(CPM)
MAX_WILD_LEVEL = 50.0  # level 51 needs a Best Buddy boost; still accepted in feasibility checks


def calc_cp(base: Tuple[int, int, int], ivs: Tuple[int, int, int], level: float) -> int:
    a, d, s = base
    ia, idf, ist = ivs
    m = CPM[level]
    cp = math.floor((a + ia) * math.sqrt(d + idf) * math.sqrt(s + ist) * m * m / 10.0)
    return max(10, cp)


def calc_hp(base: Tuple[int, int, int], ivs: Tuple[int, int, int], level: float) -> int:
    return max(10, math.floor((base[2] + ivs[2]) * CPM[level]))


def stat_product(base: Tuple[int, int, int], ivs: Tuple[int, int, int], level: float) -> float:
    m = CPM[level]
    atk = (base[0] + ivs[0]) * m
    df = (base[1] + ivs[1]) * m
    hp = max(10, math.floor((base[2] + ivs[2]) * m))
    return atk * df * hp


@dataclass(frozen=True)
class Move:
    move_id: str
    name: str
    type: str
    power: float
    energy: float
    energy_gain: float
    turns: int


@dataclass
class Species:
    species_id: str
    dex: int
    name: str
    base_name: str
    form: Optional[str]
    atk: int
    df: int
    sta: int
    types: Tuple[str, ...]
    fast_moves: Tuple[str, ...]
    charged_moves: Tuple[str, ...]
    tags: Tuple[str, ...]
    family_id: Optional[str]
    parent: Optional[str]
    evolutions: Tuple[str, ...]
    released: bool
    shadow_eligible: bool
    level_floor: Optional[float] = None
    aliases: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def base(self) -> Tuple[int, int, int]:
        return (self.atk, self.df, self.sta)

    @property
    def is_legendary(self) -> bool:
        return "legendary" in self.tags

    @property
    def is_mythical(self) -> bool:
        return "mythical" in self.tags

    @property
    def is_ultra_beast(self) -> bool:
        return "ultrabeast" in self.tags

    @property
    def is_regional_form(self) -> bool:
        return any(t in self.tags for t in ("alolan", "galarian", "hisuian", "paldean"))

    @property
    def display_name(self) -> str:
        return f"{self.base_name} ({self.form})" if self.form else self.base_name


_FORM_RE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")


class GameData:
    def __init__(self, path: Path):
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        self.path = path
        self.sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self.timestamp: str = raw.get("timestamp", "")
        self.source = "PvPoke gamemaster.json (MIT)"
        self.moves: Dict[str, Move] = {}
        for m in raw.get("moves", []):
            self.moves[m["moveId"]] = Move(
                move_id=m["moveId"], name=m.get("name", m["moveId"]), type=m.get("type", "normal"),
                power=float(m.get("power", 0)), energy=float(m.get("energy", 0)),
                energy_gain=float(m.get("energyGain", 0)), turns=int(m.get("turns", 1) or 1),
            )
        shadow_ids = {p["speciesId"][: -len("_shadow")] for p in raw["pokemon"] if p["speciesId"].endswith("_shadow")}
        self.species: Dict[str, Species] = {}
        for p in raw["pokemon"]:
            sid = p["speciesId"]
            tags = tuple(p.get("tags", []))
            if sid.endswith("_shadow") or "mega" in tags or "supermega" in tags or "_mega" in sid or "_primal" in sid:
                continue
            m = _FORM_RE.match(p["speciesName"])
            base_name, form = (m.group(1), m.group(2)) if m else (p["speciesName"], None)
            fam = p.get("family") or {}
            bs = p["baseStats"]
            self.species[sid] = Species(
                species_id=sid, dex=int(p["dex"]), name=p["speciesName"], base_name=base_name, form=form,
                atk=int(bs["atk"]), df=int(bs["def"]), sta=int(bs["hp"]), types=tuple(t for t in p.get("types", []) if t != "none"),
                fast_moves=tuple(p.get("fastMoves", [])), charged_moves=tuple(p.get("chargedMoves", [])), tags=tags,
                family_id=fam.get("id"), parent=fam.get("parent"), evolutions=tuple(fam.get("evolutions", [])),
                released=bool(p.get("released", True)), shadow_eligible=sid in shadow_ids or "shadoweligible" in tags,
                level_floor=p.get("levelFloor"), aliases=tuple(p.get("nicknames", []) or []),
            )
        self.by_family: Dict[str, List[Species]] = {}
        for s in self.species.values():
            if s.family_id:
                self.by_family.setdefault(s.family_id, []).append(s)
        self.by_base_name: Dict[str, List[Species]] = {}
        for s in self.species.values():
            self.by_base_name.setdefault(s.base_name.upper(), []).append(s)

    @property
    def data_date(self) -> Optional[datetime]:
        try:
            return datetime.strptime(self.timestamp[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        d = self.data_date
        if d is None:
            return True
        now = now or datetime.utcnow()
        return (now - d).days > settings.gamedata_stale_days

    def get(self, species_id: Optional[str]) -> Optional[Species]:
        return self.species.get(species_id) if species_id else None

    def default_form(self, base_name: str) -> Optional[Species]:
        """The form a plain species name refers to (e.g. 'Rattata' -> rattata, not rattata_alolan)."""
        options = self.by_base_name.get(base_name.upper(), [])
        if not options:
            return None
        plain = [s for s in options if s.form is None]
        return plain[0] if plain else sorted(options, key=lambda s: len(s.species_id))[0]

    def family_members(self, base_name: str) -> List[Species]:
        sp = self.default_form(base_name)
        if sp is None or not sp.family_id:
            return [sp] if sp else []
        return self.by_family.get(sp.family_id, [])


@lru_cache(maxsize=1)
def get_gamedata() -> GameData:
    return GameData(settings.gamemaster_path)
