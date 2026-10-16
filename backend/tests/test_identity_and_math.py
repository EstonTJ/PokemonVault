import json

import cv2
import numpy as np

from app.appraisal.bars import iv_candidates, read_bar
from app.catalog.identity import find_candidates
from app.config import settings
from app.recognition.feasibility import feasible, is_feasible
from app.recognition.species import identify
from app.recommend.gamedata import calc_cp, calc_hp, get_gamedata


def A(v, c=0.95):
    return {"value": v, "confidence": c}


EXISTING = [
    {"id": "p1", "species_id": "pidgey", "cp": 500, "hp": 60, "nickname": None, "shiny": None, "shadow": None,
     "purified": None, "lucky": None, "iv_atk": None},
    {"id": "p2", "species_id": "pidgey", "cp": 500, "hp": 61, "nickname": "Birb", "shiny": False, "shadow": None,
     "purified": None, "lucky": None, "iv_atk": None},
]


def test_species_and_cp_alone_only_produce_candidates():
    cands = find_candidates({"species_id": A("pidgey"), "cp": A(500)}, EXISTING)
    assert {c.pokemon_id for c in cands} == {"p1", "p2"}
    assert all(c.score < 0.5 for c in cands)


def test_contradicting_fields_exclude_candidates():
    assert [c.pokemon_id for c in find_candidates({"species_id": A("pidgey"), "cp": A(500), "hp": A(60)}, EXISTING)] == ["p1"]
    # A stored Pokémon without a nickname may have been nicknamed since, so it stays a (weaker) candidate.
    assert [c.pokemon_id for c in find_candidates({"species_id": A("pidgey"), "cp": A(500), "nickname": A("Birb")}, EXISTING)] == ["p2", "p1"]
    assert [c.pokemon_id for c in find_candidates({"species_id": A("pidgey"), "cp": A(500), "nickname": A("Other")}, EXISTING)] == ["p1"]
    assert find_candidates({"species_id": A("pidgey"), "cp": A(500), "shiny": A(True)}, EXISTING)[0].pokemon_id == "p1"
    assert find_candidates({"species_id": A("pidgey"), "cp": A(501)}, EXISTING) == []


def test_low_confidence_readings_do_not_match():
    assert find_candidates({"species_id": A("pidgey", 0.3), "cp": A(500)}, EXISTING) == []


def test_cp_formula_matches_reference_data():
    raw = json.loads(settings.gamemaster_path.read_bytes())
    checked = mismatched = 0
    for p in raw["pokemon"]:
        if "level25CP" not in p or "_mega" in p["speciesId"]:
            continue
        b = p["baseStats"]
        checked += 1
        mismatched += calc_cp((b["atk"], b["def"], b["hp"]), (0, 0, 0), 25.0) != p["level25CP"]
    assert checked > 400
    assert mismatched / checked < 0.03


def test_feasibility_distinguishes_forms():
    gd = get_gamedata()
    alolan = gd.get("rattata_alolan")
    cp = calc_cp(alolan.base, (15, 15, 15), 30.0)
    hp = calc_hp(alolan.base, (15, 15, 15), 30.0)
    assert is_feasible(alolan, cp, hp)
    sols = feasible(alolan, cp, hp)
    assert (30.0, 15, 15, 15) in sols


def test_identify_nickname_uses_candy_and_feasibility():
    gd = get_gamedata()
    sp = gd.get("ivysaur")
    cp, hp = calc_cp(sp.base, (10, 10, 10), 25.0), calc_hp(sp.base, (10, 10, 10), 25.0)
    r = identify(gd, "Leafy", 0.95, cp=cp, hp=hp, candy_family="BULBASAUR")
    assert r.nickname == "Leafy"
    assert r.species is not None and r.species.species_id in ("ivysaur",) or r.candidates


def test_identify_never_guesses_without_evidence():
    gd = get_gamedata()
    r = identify(gd, "Sparky", 0.95, cp=637)
    assert r.species is None
    assert r.nickname == "Sparky"


def _bar_strip(iv, length=500, perfect_color=True):
    img = np.full((60, 720, 3), 255, np.uint8)
    x, y, t = 110, 20, 22
    cv2.rectangle(img, (x, y), (x + length, y + t), (205, 205, 205), -1)
    fill = int(round(length * iv / 15.0))
    color = (110, 114, 232) if (iv == 15 and perfect_color) else (61, 159, 247)
    if fill:
        cv2.rectangle(img, (x, y), (x + fill - 1, y + t), color, -1)
    for k in (1, 2):
        sx = x + int(length * k / 3)
        cv2.rectangle(img, (sx - 2, y), (sx + 1, y + t), (255, 255, 255), -1)
    return img


def test_bar_reader_all_values():
    for iv in range(16):
        r = read_bar(_bar_strip(iv), "atk", 720)
        assert r.lo <= iv <= r.hi, (iv, r)
        assert r.lo == r.hi == iv, (iv, r)


def test_bar_reader_flags_boundary_as_range():
    img = np.full((60, 720, 3), 255, np.uint8)
    cv2.rectangle(img, (110, 20), (610, 42), (205, 205, 205), -1)
    cv2.rectangle(img, (110, 20), (110 + int(500 * 7.5 / 15) - 1, 42), (61, 159, 247), -1)  # exactly between 7 and 8
    r = read_bar(img, "atk", 720)
    assert (r.lo, r.hi) == (7, 8)
    assert r.confidence <= 0.5


def test_iv_candidates_verified_only_when_unique():
    gd = get_gamedata()
    sp = gd.get("bulbasaur")
    readings = {k: read_bar(_bar_strip(v), k, 720) for k, v in zip(("atk", "def", "sta"), (10, 12, 14))}
    cp, hp = calc_cp(sp.base, (10, 12, 14), 20.0), calc_hp(sp.base, (10, 12, 14), 20.0)
    cands, status, conf, _ = iv_candidates(readings, sp, cp, hp)
    assert status == "verified" and cands == [(10, 12, 14)]
    # Wrong CP: inconsistent -> never "verified"
    _, status2, _, reasons = iv_candidates(readings, sp, cp + 37, hp)
    assert status2 != "verified"
