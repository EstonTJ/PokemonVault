"""Which (level, IV) combinations can produce an observed CP and HP for a species?

This lets the app check species and form hypotheses (e.g. Rattata vs Alolan
Rattata) against hard game math instead of guessing, and it narrows appraisal
IV readings.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Tuple

import numpy as np

from ..recommend.gamedata import CPM, LEVELS, Species

_IV = np.arange(16)
_A, _D, _S = np.meshgrid(_IV, _IV, _IV, indexing="ij")
_A, _D, _S = _A.ravel(), _D.ravel(), _S.ravel()
_LEVEL_ARR = np.array(LEVELS)
_CPM_ARR = np.array([CPM[lv] for lv in LEVELS])


@lru_cache(maxsize=4096)
def _tables(atk: int, df: int, sta: int):
    """CP and HP for every (level, iv) combination: arrays of shape (levels, 4096)."""
    m = _CPM_ARR[:, None]
    a = (atk + _A)[None, :]
    d = (df + _D)[None, :]
    s = (sta + _S)[None, :]
    cp = np.floor(a * np.sqrt(d) * np.sqrt(s) * m * m / 10.0)
    cp = np.maximum(cp, 10).astype(np.int32)
    hp = np.maximum(np.floor(s * m), 10).astype(np.int32)
    return cp, hp


def feasible(
    species: Species,
    cp: int,
    hp: Optional[int] = None,
    iv_bounds: Optional[Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = None,
    min_iv: int = 0,
    max_level: float = 51.0,
) -> List[Tuple[float, int, int, int]]:
    """All (level, atk_iv, def_iv, sta_iv) consistent with the observations."""
    cp_t, hp_t = _tables(species.atk, species.df, species.sta)
    mask = cp_t == cp
    if hp is not None:
        mask &= hp_t == hp
    mask &= (_LEVEL_ARR <= max_level)[:, None]
    ivmask = (_A >= min_iv) & (_D >= min_iv) & (_S >= min_iv)
    if iv_bounds is not None:
        (a0, a1), (d0, d1), (s0, s1) = iv_bounds
        ivmask &= (_A >= a0) & (_A <= a1) & (_D >= d0) & (_D <= d1) & (_S >= s0) & (_S <= s1)
    mask &= ivmask[None, :]
    li, ii = np.nonzero(mask)
    return [(float(_LEVEL_ARR[l]), int(_A[i]), int(_D[i]), int(_S[i])) for l, i in zip(li, ii)]


def is_feasible(species: Species, cp: int, hp: Optional[int] = None) -> bool:
    cp_t, hp_t = _tables(species.atk, species.df, species.sta)
    mask = cp_t == cp
    if hp is not None:
        mask &= hp_t == hp
    return bool(mask.any())
