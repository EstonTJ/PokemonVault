"""Map OCR'd names (and supporting evidence) to canonical species and forms."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..ocr.parse import best_name_matches, name_similarity
from ..recommend.gamedata import GameData, Species
from .feasibility import is_feasible

SPECIES_MATCH_MIN = 0.84   # name similarity needed to treat text as a species name
NICKNAME_MAX = 0.70        # below this the text is treated as a nickname rather than a misread


@dataclass
class SpeciesResult:
    species: Optional[Species]
    species_conf: float
    form_conf: float
    nickname: Optional[str]
    nickname_conf: float
    method: str
    candidates: List[str] = field(default_factory=list)  # species_ids still possible
    notes: List[str] = field(default_factory=list)


def _base_names(gd: GameData) -> List[str]:
    return list(gd.by_base_name.keys())


def resolve_forms(gd: GameData, base_name: str, cp: Optional[int], hp: Optional[int]) -> List[Species]:
    """All forms sharing the base name that remain consistent with the CP and HP."""
    forms = gd.by_base_name.get(base_name.upper(), [])
    if cp is None:
        return forms
    return [s for s in forms if is_feasible(s, cp, hp)]


TYPES = ("normal", "fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground", "flying", "psychic",
         "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy")


def parse_types(text: Optional[str]) -> Optional[List[Optional[str]]]:
    """'DARK/NORMAL' -> ['dark', 'normal']; truncated 'BUG/STE' -> ['bug', 'steel']; unreadable parts -> None."""
    if not text:
        return None
    parts = [p.strip().lower() for p in text.replace("|", "/").split("/")]
    out: List[Optional[str]] = []
    for part in parts[:2]:
        part = "".join(c for c in part if c.isalpha())
        hits = [t for t in TYPES if len(part) >= 3 and t.startswith(part)] or [t for t in TYPES if len(part) >= 4 and part.startswith(t)]
        out.append(hits[0] if len(hits) == 1 else None)
    return out if any(out) else None


def _types_fit(sp: Species, types: Optional[List[Optional[str]]]) -> bool:
    if not types:
        return True
    st = list(sp.types)
    if len(types) == 2 and types[1] is not None and len(st) < 2:
        return False
    return all(t is None or (i < len(st) and st[i] == t) for i, t in enumerate(types))


def _resolve(gd: GameData, base: str, cp: Optional[int], hp: Optional[int], types) -> List[Species]:
    forms = resolve_forms(gd, base, cp, hp)
    if types:
        typed = [s for s in forms if _types_fit(s, types)]
        if typed:
            return typed
    return forms


def _from_base(gd: GameData, base: str, conf: float, method: str, cp, hp, types, nickname, nick_conf,
               notes: List[str]) -> SpeciesResult:
    all_forms = gd.by_base_name.get(base, [])
    forms = _resolve(gd, base, cp, hp, types)
    if not forms:
        # The species reads clearly, but no level/IV combination fits the CP/HP. Something was misread.
        notes.append(f"CP/HP are not achievable for {base.title()}; check CP or HP.")
        return SpeciesResult(gd.default_form(base), round(conf * 0.5, 3), 0.2, nickname, nick_conf, method + ":infeasible",
                             [s.species_id for s in all_forms], notes)
    if len(forms) == 1:
        if len(all_forms) == 1:
            form_conf = 0.95
        else:
            typed = bool(types) and len([s for s in all_forms if _types_fit(s, types)]) == 1
            form_conf = 0.9 if typed else (0.8 if hp is not None else 0.6)
            notes.append(f"Form chosen by {'type line' if typed else 'CP/HP feasibility'} among {len(all_forms)} forms.")
        return SpeciesResult(forms[0], round(conf, 3), form_conf, nickname, nick_conf, method, [forms[0].species_id], notes)
    default = next((s for s in forms if s.form is None), forms[0])
    notes.append("Form could not be determined: " + ", ".join(s.display_name for s in forms))
    return SpeciesResult(default, round(conf, 3), 0.3, nickname, nick_conf, method + ":form-ambiguous",
                         [s.species_id for s in forms], notes)


def identify(
    gd: GameData,
    name_text: Optional[str],
    name_conf: float,
    cp: Optional[int] = None,
    hp: Optional[int] = None,
    candy_family: Optional[str] = None,
    types: Optional[List[Optional[str]]] = None,
    caption_species: Optional[str] = None,
    caption_conf: float = 0.0,
) -> SpeciesResult:
    """Species/form from, in order of strength: the name (when it is a species name), the in-game
    "This <species> was caught…" caption, then the candy family. Forms are narrowed by the type line
    and by CP/HP feasibility. Nothing is guessed: unresolved cases return candidates and notes."""
    notes: List[str] = []
    if name_text:
        (best, sim), = best_name_matches(name_text, _base_names(gd), limit=1)
        if sim >= SPECIES_MATCH_MIN:
            return _from_base(gd, best, name_conf * sim, "name", cp, hp, types, None, 0.0, notes)

    # Not a species name, so it is treated as a nickname (or an unreadable name).
    nickname = name_text.strip() if name_text else None
    nick_conf = 0.0
    if nickname:
        (best, sim), = best_name_matches(nickname, _base_names(gd), limit=1)
        nick_conf = round(name_conf * (0.9 if sim < NICKNAME_MAX else 0.5), 3)
        if sim >= NICKNAME_MAX:
            notes.append(f"Name '{nickname}' is close to {best.title()}; may be a misread rather than a nickname.")

    if caption_species:
        (best, sim), = best_name_matches(caption_species, _base_names(gd), limit=1)
        if sim >= SPECIES_MATCH_MIN:
            if nickname:
                nick_conf = max(nick_conf, round(name_conf * 0.9, 3))
                notes = [n for n in notes if "misread" not in n]
            return _from_base(gd, best, caption_conf * sim * 0.98, "caption", cp, hp, types, nickname, nick_conf, notes)

    if candy_family:
        members = gd.family_members(candy_family)
        if not members:
            (fam, fsim), = best_name_matches(candy_family, _base_names(gd), limit=1)
            if fsim >= SPECIES_MATCH_MIN:
                members = gd.family_members(fam)
        if cp is not None:
            members = [s for s in members if is_feasible(s, cp, hp)]
        if types:
            members = [s for s in members if _types_fit(s, types)] or members
        if len(members) == 1:
            notes.append(f"Species inferred from candy family and CP{'/HP' if hp is not None else ''} feasibility.")
            return SpeciesResult(members[0], 0.75 if hp is not None else 0.6, 0.7, nickname, nick_conf,
                                 "candy+feasibility", [members[0].species_id], notes)
        if members:
            notes.append("Several family members fit: " + ", ".join(s.display_name for s in members[:6]))
            return SpeciesResult(None, 0.0, 0.0, nickname, nick_conf, "candy:ambiguous", [s.species_id for s in members], notes)

    notes.append("Species could not be identified from the visible text.")
    return SpeciesResult(None, 0.0, 0.0, nickname, nick_conf, "unidentified", [], notes)


def is_default_name(gd: GameData, species: Optional[Species], name_text: Optional[str]) -> bool:
    if not species or not name_text:
        return False
    return name_similarity(species.base_name, name_text) >= SPECIES_MATCH_MIN
