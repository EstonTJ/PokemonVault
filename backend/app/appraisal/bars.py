"""Read in-game appraisal bars and turn them into IV *candidates*.

Each bar is a light-grey track partly covered by a warm fill (orange; a pink/red
fill marks a perfect 15). The reader measures fill / track length and maps it to
0..15. Readings close to a boundary between two values become a range, not a
guess. Exact IVs are reported only when every stat resolves to a single value,
either directly or after cross-checking against CP/HP feasibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..ocr.engine import OcrToken
from ..ocr.parse import normalize
from ..recommend.gamedata import Species
from ..recognition import layout as L
from ..recognition.feasibility import feasible

AMBIGUITY_MARGIN = 0.28   # distance from x.5 (in IV units) below which a reading is ambiguous
MIN_TRACK_FRAC = 0.25     # track must span at least this fraction of the frame width


@dataclass
class BarReading:
    stat: str
    ratio: Optional[float]
    estimate: Optional[float]
    lo: Optional[int]
    hi: Optional[int]
    confidence: float
    perfect_color: bool
    track_px: int

    def to_json(self) -> dict:
        return self.__dict__.copy()


def _masks(region_bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    warm = ((h <= 25) | (h >= 160)) & (s >= 90) & (v >= 120)
    pinkred = ((h <= 6) | (h >= 160)) & (s >= 70) & (v >= 120)
    track = (s <= 40) & (v >= 150) & (v <= 238)
    return warm, pinkred, track


def read_bar(strip_bgr: np.ndarray, stat: str, frame_width: int) -> BarReading:
    """Analyse a horizontal strip that should contain exactly one appraisal bar."""
    warm, pinkred, track = _masks(strip_bgr)
    rows_score = (warm | track).sum(axis=1)
    if rows_score.max() < frame_width * MIN_TRACK_FRAC:
        return BarReading(stat, None, None, None, None, 0.0, False, 0)
    strong = np.nonzero(rows_score >= rows_score.max() * 0.8)[0]
    r = int(np.median(strong))  # vertical centre of the bar, not its edge
    band = slice(max(0, r - 2), min(strip_bgr.shape[0], r + 3))
    col_fill = warm[band].mean(axis=0) > 0.5
    col_track = track[band].mean(axis=0) > 0.5
    col_any = col_fill | col_track
    xs = np.nonzero(col_any)[0]
    if xs.size == 0:
        return BarReading(stat, None, None, None, None, 0.0, False, 0)
    # Accept separator gaps up to 2.5% of the width between segments.
    max_gap = max(3, int(frame_width * 0.025))
    start = xs[0]
    end = xs[0]
    for x in xs[1:]:
        if x - end > max_gap:
            break
        end = x
    track_len = end - start + 1
    if track_len < frame_width * MIN_TRACK_FRAC:
        return BarReading(stat, None, None, None, None, 0.0, False, int(track_len))
    fill_xs = np.nonzero(col_fill[start : end + 1])[0]
    fill_len = (fill_xs[-1] + 1) if fill_xs.size else 0
    ratio = float(fill_len) / float(track_len)
    perfect = bool(fill_xs.size and pinkred[band].mean(axis=0)[start : start + fill_len].mean() > 0.6)
    est = ratio * 15.0
    if perfect and ratio > 0.97:
        return BarReading(stat, ratio, 15.0, 15, 15, 0.97, True, int(track_len))
    nearest = int(round(est))
    margin = abs(est - nearest)
    # Resolution: one IV point is track_len/15 pixels; small tracks are less trustworthy.
    px_per_iv = track_len / 15.0
    res_penalty = 1.0 if px_per_iv >= 12 else px_per_iv / 12.0
    if margin <= 0.5 - AMBIGUITY_MARGIN:
        lo = hi = max(0, min(15, nearest))
        conf = (1.0 - margin * 1.5) * res_penalty
    else:
        lo, hi = max(0, int(np.floor(est))), min(15, int(np.ceil(est)))
        conf = 0.5 * res_penalty
    if not perfect and hi == 15 and lo == 15:
        # A full bar that is not the perfect colour is suspicious; widen it.
        lo, conf = 14, conf * 0.6
    return BarReading(stat, round(ratio, 4), round(est, 2), lo, hi, round(conf, 3), perfect, int(track_len))


def _attack_label(tokens: List[OcrToken], h: int) -> Optional[OcrToken]:
    cands = [t for t in tokens if t.cy > h * L.APPRAISAL_BAND[0] and normalize(t.text).upper().startswith("ATTACK")
             and len(normalize(t.text)) <= 8]
    return max(cands, key=lambda t: t.cy) if cands else None


def locate_and_read(frame_bgr: np.ndarray, tokens: List[OcrToken]) -> Dict[str, BarReading]:
    """Find the Attack/Defense/HP labels and read the bar under each, inside the appraisal panel only."""
    h, w = frame_bgr.shape[:2]
    out: Dict[str, BarReading] = {}
    labels = {"atk": "ATTACK", "def": "DEFENSE", "sta": "HP"}
    for key, word in labels.items():
        cands = [t for t in tokens if t.cy > h * L.APPRAISAL_BAND[0] and normalize(t.text).upper().startswith(word)
                 and len(normalize(t.text)) <= len(word) + 2]
        if not cands:
            out[key] = BarReading(key, None, None, None, None, 0.0, False, 0)
            continue
        t = max(cands, key=lambda t: t.cy)
        # The bar sits directly under its label, left-aligned with it. Limiting the strip to the panel keeps
        # team-coloured backgrounds and the team leader's artwork out of the measurement.
        y0, y1 = int(t.y1), int(min(h, t.y1 + max(t.h * 1.6, 18)))
        x0, x1 = int(max(0, t.x0 - 0.02 * w)), int(min(w, t.x0 + 0.45 * w))
        out[key] = read_bar(frame_bgr[y0:y1, x0:x1], key, w)
    return out


# Sum-of-IV ranges for the appraisal star badge.
STAR_TOTALS = {0: (0, 22), 1: (23, 29), 2: (30, 36), 3: (37, 45)}


def read_stars(frame_bgr: np.ndarray, tokens: List[OcrToken]) -> Tuple[Optional[int], float]:
    """Count filled stars on the appraisal badge (filled = orange, empty = grey).

    The badge sits above and to the left of the 'Attack' label. A filled star is a compact orange shape about
    5% of the frame width across, with a star-like fill ratio (area / bounding box ~0.5). This was calibrated
    on real 3/2/1/0-star badges. The red "perfect" badge has not been seen yet, so 3 stars is reported as
    37-45 and the bars decide 45.

    Stars animate in one at a time, so a single frame can show fewer stars than the final rating. Callers
    must treat the count as a MINIMUM unless it agrees with the bars.
    """
    h, w = frame_bgr.shape[:2]
    a = _attack_label(tokens, h)
    if a is None:
        return None, 0.0
    y0, y1 = int(max(0, a.y0 - 0.30 * w)), int(max(0, a.y0 - 0.005 * w))
    x1 = int(min(w, a.x0 + 0.20 * w))
    roi = frame_bgr[y0:y1, 0:x1]
    if roi.size == 0:
        return None, 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh, ss, vv = (hsv[..., i].astype(int) for i in range(3))
    lo, hi = 0.045 * w, 0.09 * w  # stars 'pop' slightly larger while animating in

    def count(mask: np.ndarray) -> int:
        n = 0
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            _, _, bw, bh = cv2.boundingRect(c)
            if not (lo <= bw <= hi and lo <= bh <= hi and 0.7 <= bw / float(bh) <= 1.4):
                continue
            area = cv2.contourArea(c)
            hull = cv2.contourArea(cv2.convexHull(c))
            # A five-pointed star fills ~50-70% of its convex hull and ~45-60% of its box; badge stripes and
            # panel corners don't.
            if hull > 0 and 0.40 <= area / hull <= 0.78 and 0.42 <= area / float(bw * bh) <= 0.62:
                n += 1
        return n

    filled = count(((hh <= 25) | (hh >= 160)) & (ss >= 120) & (vv >= 170))
    empty = count((ss <= 40) & (vv >= 150) & (vv <= 235))
    # Only trust the count when all three star slots were found (filled or empty): that proves a badge is there.
    if filled + empty != 3:
        return None, 0.0
    return filled, 0.9


def iv_candidates(readings: Dict[str, BarReading], species: Optional[Species], cp: Optional[int], hp: Optional[int],
                  stars: Optional[int] = None) -> Tuple[List[Tuple[int, int, int]], str, float, List[str]]:
    """Combine bar ranges with CP/HP feasibility.

    Returns (candidates, status, confidence, reasons). The status is 'verified'
    only when exactly one IV triple remains and every bar was read.
    """
    reasons: List[str] = []
    if any(readings.get(k) is None or readings[k].lo is None for k in ("atk", "def", "sta")):
        return [], "failed", 0.0, ["One or more appraisal bars could not be read."]
    ranges = [(readings[k].lo, readings[k].hi) for k in ("atk", "def", "sta")]
    combos = [c for c in product(*[range(lo, hi + 1) for lo, hi in ranges])]
    bar_conf = min(readings[k].confidence for k in ("atk", "def", "sta"))
    if stars is not None and stars in STAR_TOTALS:
        lo_t, hi_t = STAR_TOTALS[stars]
        starred = [c for c in combos if lo_t <= sum(c) <= hi_t]
        at_least = [c for c in combos if sum(c) >= lo_t]
        if starred:
            if len(starred) < len(combos):
                reasons.append(f"{stars}-star rating narrowed {len(combos)} bar combinations to {len(starred)}.")
            combos = starred
        elif at_least:
            # Bars exceed the tier: the star animation hadn't finished in this frame. Use it as a minimum.
            reasons.append(f"Star badge was still animating ({stars} shown); used as a minimum.")
            combos = at_least
        else:
            reasons.append(f"Bars are lower than the {stars}-star rating allows; check the appraisal frame.")
            bar_conf *= 0.5
        ranges = [(min(c[i] for c in combos), max(c[i] for c in combos)) for i in range(3)]
    if species is not None and cp is not None:
        f = feasible(species, cp, hp, iv_bounds=tuple(ranges))  # type: ignore[arg-type]
        allowed = set(combos)
        feasible_ivs = sorted({(a, d, s) for _, a, d, s in f} & allowed)
        if not feasible_ivs:
            reasons.append("Bar readings are inconsistent with CP/HP; re-check the appraisal frame or the CP.")
            return combos, "ambiguous", bar_conf * 0.3, reasons
        if len(feasible_ivs) < len(combos):
            reasons.append(f"CP{'/HP' if hp else ''} feasibility narrowed {len(combos)} bar combinations to {len(feasible_ivs)}.")
        combos = feasible_ivs
    else:
        reasons.append("Species or CP unknown; IVs not cross-checked against CP.")
    if len(combos) == 1 and bar_conf >= 0.5:
        return combos, "verified", round(min(0.99, 0.5 + bar_conf / 2), 3), reasons
    reasons.append(f"{len(combos)} IV combinations remain; confirm visually.")
    return combos, "ambiguous", round(bar_conf * 0.7, 3), reasons
