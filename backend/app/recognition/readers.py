"""Turn OCR tokens of one normalized frame into structured observations.

Screen classification and both readers work only on token geometry and text,
so they can be unit-tested with synthetic tokens and stay independent of the
OCR engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional, Tuple

from ..ocr.engine import OcrToken
from ..ocr.parse import (contains_word, is_cp_label, looks_like_name, normalize, parse_candy_family, parse_cp,
                         parse_cp_number, parse_hp)
from ..recommend.gamedata import GameData
from . import layout as L
import re

from .species import identify, is_default_name, parse_types


@dataclass
class Attr:
    value: object
    confidence: float
    method: str

    def to_json(self, frame_id: Optional[int]) -> dict:
        return {"value": self.value, "confidence": round(float(self.confidence), 3), "method": self.method, "frame_id": frame_id}


@dataclass
class RawObservation:
    screen_type: str
    bbox: Tuple[float, float, float, float]
    attrs: Dict[str, Attr] = field(default_factory=dict)
    grid_col: Optional[int] = None
    frame_y: Optional[float] = None      # vertical anchor in frame pixels (grid: CP label top)
    cell_pitch: Optional[float] = None   # estimated grid row pitch (pixels)
    notes: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)


# --------------------------------------------------------------------- screen type

def classify(tokens: List[OcrToken], width: int, height: int) -> str:
    texts = [normalize(t.text).upper() for t in tokens]
    cp_hits = _cp_entries(tokens)
    lower = [normalize(t.text).upper() for t in tokens if t.cy > height * L.APPRAISAL_BAND[0]]
    appraisal_hits = sum(1 for k in L.APPRAISAL_KEYWORDS if any(x == k or x.startswith(k) for x in lower))
    if appraisal_hits >= 3:
        return "appraisal"
    detail_hits = sum(1 for k in L.DETAIL_KEYWORDS if any(k in t for t in texts))
    top_cp = [c for c in cp_hits if c[1].y0 < height * L.DETAIL_CP_MAX_Y]
    if detail_hits >= L.DETAIL_MIN_KEYWORDS and len(cp_hits) <= 2 and top_cp:
        return "detail"
    content_cps = [c for c in cp_hits if height * L.GRID_CONTENT_TOP < c[1].cy < height * L.GRID_CONTENT_BOTTOM]
    if len(content_cps) >= L.GRID_MIN_CP_TOKENS:
        cols = {_column(c[1].cx, width) for c in content_cps}
        if len(cols) >= 2:
            return "grid"
    return "other"


def _column(cx: float, width: int) -> int:
    return max(0, min(L.GRID_COLUMNS - 1, int(cx / (width / L.GRID_COLUMNS))))


def _cp_entries(tokens: List[OcrToken]) -> List[Tuple[object, OcrToken]]:
    """Find CP values as (Parsed, anchor_token). Handles 'CP1234' and split 'CP' + '1234'."""
    out = []
    used = set()
    for i, t in enumerate(tokens):
        p = parse_cp(t.text, t.conf)
        if p:
            out.append((p, t))
            used.add(i)
    for i, t in enumerate(tokens):
        if i in used or not is_cp_label(t.text):
            continue
        best_j, best_dx = None, None
        for j, u in enumerate(tokens):
            if j == i or j in used:
                continue
            if abs(u.cy - t.cy) > max(t.h, u.h) * 0.6:
                continue
            dx = u.x0 - t.x1
            if -t.h * 0.8 <= dx <= t.h * 2.5 and (best_dx is None or dx < best_dx):
                best_j, best_dx = j, dx
        if best_j is not None:
            u = tokens[best_j]
            p = parse_cp_number(u.text, min(t.conf, u.conf))
            if p:
                merged = OcrToken(f"CP {u.text}", p.confidence, t.x0, min(t.y0, u.y0), u.x1, max(t.y1, u.y1))
                out.append((p, merged))
                used.update({i, best_j})
    return out


# --------------------------------------------------------------------- grid

def read_grid(tokens: List[OcrToken], width: int, height: int, gd: GameData) -> List[RawObservation]:
    cps = [(p, t) for p, t in _cp_entries(tokens) if height * L.GRID_CONTENT_TOP < t.cy < height * L.GRID_CONTENT_BOTTOM]
    if not cps:
        return []
    col_w = width / L.GRID_COLUMNS
    # Row pitch from vertical spacing of CP labels in the same column.
    gaps = []
    by_col: Dict[int, List[float]] = {}
    for _, t in cps:
        by_col.setdefault(_column(t.cx, width), []).append(t.y0)
    for ys in by_col.values():
        ys.sort()
        gaps.extend(b - a for a, b in zip(ys, ys[1:]) if b - a > col_w * 0.5)
    pitch = median(gaps) if gaps else col_w * 1.25

    name_tokens = [t for t in tokens if looks_like_name(t.text) and normalize(t.text).upper() not in L.NON_NAME_WORDS]
    obs: List[RawObservation] = []
    for p, t in cps:
        col = _column(t.cx, width)
        x0, x1 = col * col_w, (col + 1) * col_w
        # The name sits below the sprite, inside the same column and above the next row's CP label.
        limit = t.y0 + min(pitch * 0.98, col_w * L.GRID_NAME_MAX_BELOW)
        below = [n for n in name_tokens if x0 <= n.cx < x1 and t.y1 < n.cy < limit]
        name = max(below, key=lambda n: n.cy) if below else None
        bottom = name.y1 if name else min(height, t.y0 + pitch * 0.9)
        cell_complete = name is not None and bottom < height * L.GRID_CONTENT_BOTTOM
        ob = RawObservation("grid", (x0, max(0.0, t.y0 - 4), x1, bottom + 4), grid_col=col, frame_y=t.y0, cell_pitch=pitch)
        ob.attrs["cp"] = Attr(p.value, p.confidence, p.method)
        if name is not None:
            name_conf = name.conf
            if name.y1 > height * (L.GRID_CONTENT_BOTTOM - 0.012) or name.y0 < height * L.GRID_CONTENT_TOP:
                # Clipped at the edge of the scroll area: text is likely truncated.
                name_conf *= 0.3
                ob.notes.append("Cell partially visible.")
            sr = identify(gd, name.text, name_conf, cp=int(p.value))
            _apply_species(ob, sr, name.text, name_conf, gd)
        else:
            ob.notes.append("Name not visible in this frame.")
        if not cell_complete and "Cell partially visible." not in ob.notes:
            ob.notes.append("Cell partially visible.")
        obs.append(ob)
    return obs


# --------------------------------------------------------------------- detail

def read_detail(tokens: List[OcrToken], width: int, height: int, gd: GameData) -> List[RawObservation]:
    cps = [(p, t) for p, t in _cp_entries(tokens) if t.y0 < height * L.DETAIL_CP_MAX_Y]
    if not cps:
        return []
    p, cp_tok = max(cps, key=lambda c: c[1].h)
    texts = [t.text for t in tokens]
    ob = RawObservation("detail", (0, 0, width, height))
    ob.attrs["cp"] = Attr(p.value, p.confidence, p.method)

    hp = _find_hp(tokens)
    if hp:
        ob.attrs["hp"] = Attr(hp.value, hp.confidence, hp.method)

    candy = None
    for t in tokens:
        c = parse_candy_family(t.text, t.conf)
        if c and "XL" not in normalize(t.text).upper().split()[-1:]:
            candy = c
            break

    lo, hi = height * L.DETAIL_NAME_BAND[0], height * L.DETAIL_NAME_BAND[1]
    names = [t for t in tokens if lo < t.cy < hi and looks_like_name(t.text)
             and normalize(t.text).upper() not in L.NON_NAME_WORDS and parse_candy_family(t.text) is None
             and "/" not in t.text]
    name = max(names, key=lambda t: (t.h, -abs(t.cx - width / 2))) if names else None

    weight, height_m, type_tok = _stats_row(tokens)
    types = parse_types(type_tok.text) if type_tok else None
    caption = find_caption(tokens, height)

    sr = identify(gd, name.text if name else None, name.conf if name else 0.0, cp=int(p.value),
                  hp=int(hp.value) if hp else None, candy_family=str(candy.value) if candy else None,
                  types=types, caption_species=caption[0] if caption else None,
                  caption_conf=caption[1] if caption else 0.0)
    _apply_species(ob, sr, name.text if name else None, name.conf if name else 0.0, gd)
    if candy:
        ob.attrs["candy_family"] = Attr(candy.value, candy.confidence, candy.method)
    # Weight and height are random per individual: a strong identity fingerprint.
    if weight:
        ob.attrs["weight_kg"] = Attr(weight[0], weight[1], "ocr:weight")
    if height_m:
        ob.attrs["height_m"] = Attr(height_m[0], height_m[1], "ocr:height")

    # Text cues. These are evidence of presence only: their absence does not prove "false".
    if contains_word(texts, "LUCKY"):
        ob.attrs["lucky"] = Attr(True, 0.7, "ocr:text 'LUCKY'")
    if contains_word(texts, "PURIFY"):
        ob.attrs["shadow"] = Attr(True, 0.75, "ocr:'PURIFY' button")
        ob.attrs["purified"] = Attr(False, 0.75, "ocr:'PURIFY' button implies not purified")
    return [ob]


_KG = re.compile(r"^(\d{1,4}(?:\.\d{1,2})?)\s*kg$", re.I)
_M = re.compile(r"^(\d{1,3}(?:\.\d{1,2})?)\s*m$", re.I)
_CAPTION = re.compile(r"\bThis\s*([A-Za-z][A-Za-z.'’♀♂\- ]*?)\s*was\s*(?:caught|hatched|obtained|received|found|rescued|traded|purified|"
                      r"evolved|snapshot|encountered|discovered|befriended)", re.I)


def _stats_row(tokens: List[OcrToken]):
    """Weight ('104.88kg'), height ('2.2m') and the type text between them ('ROCK / FLYING')."""
    w = next(((float(m.group(1)), t.conf, t) for t in tokens for m in [_KG.match(normalize(t.text))] if m), None)
    h = next(((float(m.group(1)), t.conf, t) for t in tokens for m in [_M.match(normalize(t.text))] if m), None)
    type_tok = None
    if w and h:
        wt, ht = w[2], h[2]
        between = [t for t in tokens if wt.x1 < t.cx < ht.x0 and abs(t.cy - (wt.cy + ht.cy) / 2) < max(wt.h, ht.h) * 1.8
                   and re.search(r"[A-Za-z]{2}", t.text)]
        if between:
            between.sort(key=lambda t: t.x0)
            type_tok = OcrToken("/".join(x.text.strip(" /") for x in between), min(x.conf for x in between),
                                between[0].x0, between[0].y0, between[-1].x1, between[-1].y1)
    return ((w[0], w[1]) if w else None), ((h[0], h[1]) if h else None), type_tok


def caption_tokens(tokens: List[OcrToken], height: int) -> List[OcrToken]:
    """Tokens of the appraisal caption ('This Aerodactyl was caught on 7/15/2018 around Springfield…')."""
    low = sorted([t for t in tokens if t.cy > height * 0.80], key=lambda t: (round(t.cy / 12), t.x0))
    text = " ".join(t.text for t in low)
    m = _CAPTION.search(text)
    if not m:
        return []
    first = next((t for t in low if t.text.lower().startswith("this") or " this " in f" {t.text.lower()} "), None)
    if first is None:
        return []
    return [t for t in low if t.cy >= first.y0 - 2]


def find_caption(tokens: List[OcrToken], height: int):
    caps = caption_tokens(tokens, height)
    if not caps:
        return None
    m = _CAPTION.search(" ".join(t.text for t in caps))
    return (m.group(1).strip(), min(t.conf for t in caps)) if m else None


def _find_hp(tokens: List[OcrToken]):
    for t in tokens:
        r = parse_hp(t.text, t.conf)
        if r:
            return r
    # OCR often splits "123 / 145 HP" into several tokens on one line.
    rows: Dict[int, List[OcrToken]] = {}
    for t in sorted(tokens, key=lambda t: t.cy):
        key = next((k for k in rows if abs(k - t.cy) < t.h * 0.6), None)
        rows.setdefault(key if key is not None else int(t.cy), []).append(t)
    for row in rows.values():
        row.sort(key=lambda t: t.x0)
        joined = " ".join(t.text for t in row)
        r = parse_hp(joined, min(t.conf for t in row))
        if r:
            return r
    return None


def _apply_species(ob: RawObservation, sr, name_text: Optional[str], name_conf: float, gd: GameData) -> None:
    ob.notes.extend(sr.notes)
    ob.candidates = sr.candidates
    if sr.species is not None:
        ob.attrs["species_id"] = Attr(sr.species.species_id, sr.species_conf, sr.method)
        ob.attrs["dex"] = Attr(sr.species.dex, sr.species_conf, sr.method)
        ob.attrs["form"] = Attr(sr.species.form, sr.form_conf, sr.method)
    if sr.nickname:
        ob.attrs["nickname"] = Attr(sr.nickname, sr.nickname_conf, "ocr:name")
    elif name_text and sr.species is not None and not is_default_name(gd, sr.species, name_text):
        ob.attrs["nickname"] = Attr(name_text.strip(), name_conf * 0.6, "ocr:name")


def sanitize_group_attributes(attrs: Dict[str, dict], gd: GameData) -> Dict[str, dict]:
    """After consensus: drop a 'nickname' that is just a near-miss reading of the identified species name."""
    from ..ocr.parse import name_similarity

    sid = (attrs.get("species_id") or {}).get("value")
    nick = (attrs.get("nickname") or {}).get("value")
    sp = gd.get(sid)
    if sp and nick and name_similarity(sp.base_name, str(nick)) >= 0.6 and \
            attrs["species_id"].get("confidence", 0) >= attrs["nickname"].get("confidence", 0):
        attrs = dict(attrs)
        attrs.pop("nickname")
    return attrs
