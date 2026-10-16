"""Synthetic test recordings: NOT game footage.

The generator draws a simplified storage grid, a detail page and an appraisal
overlay using OpenCV primitives and Hershey fonts. It exercises the mechanics
(sampling, scroll tracking, OCR, grouping, appraisal maths) end to end. It
says nothing about accuracy on real Pokémon GO recordings.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.recommend.gamedata import calc_cp, calc_hp, get_gamedata

W, H = 720, 1560
PITCH = 300
TOP, BOTTOM = 230, 1400
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass
class Mon:
    species_id: str
    name: str
    level: float
    ivs: Tuple[int, int, int]

    @property
    def cp(self) -> int:
        return calc_cp(get_gamedata().get(self.species_id).base, self.ivs, self.level)

    @property
    def hp(self) -> int:
        return calc_hp(get_gamedata().get(self.species_id).base, self.ivs, self.level)


DEFAULT_GRID: List[Mon] = [
    Mon("bulbasaur", "Bulbasaur", 20, (10, 12, 14)), Mon("bidoof", "Bidoof", 15, (5, 5, 5)), Mon("bidoof", "Bidoof", 15, (5, 5, 5)),
    Mon("pidgey", "Pidgey", 12, (3, 9, 1)), Mon("charmander", "Charmander", 22, (15, 2, 8)), Mon("squirtle", "Squirtle", 18, (7, 7, 7)),
    Mon("pikachu", "Sparky", 25, (12, 12, 12)), Mon("geodude", "Geodude", 20, (1, 2, 3)), Mon("machop", "Machop", 30, (14, 13, 12)),
    Mon("gastly", "Gastly", 19, (9, 4, 2)), Mon("eevee", "Eevee", 23, (11, 15, 10)), Mon("magikarp", "Magikarp", 10, (0, 0, 0)),
    Mon("snorlax", "Snorlax", 30, (15, 15, 15)), Mon("dratini", "Dratini", 21, (8, 13, 6)), Mon("onix", "Onix", 17, (4, 4, 4)),
    Mon("psyduck", "Psyduck", 16, (6, 10, 3)), Mon("vulpix", "Vulpix", 24, (13, 1, 9)), Mon("abra", "Abra", 14, (2, 14, 6)),
    Mon("growlithe", "Growlithe", 27, (9, 9, 11)), Mon("ponyta", "Ponyta", 19, (4, 12, 8)), Mon("cubone", "Cubone", 21, (10, 0, 15)),
    Mon("lapras", "Lapras", 29, (14, 11, 13)), Mon("jigglypuff", "Jigglypuff", 13, (7, 3, 12)), Mon("zubat", "Zubat", 9, (1, 1, 1)),
]


def _text(img, s, x, y, scale=1.0, thick=2, color=(40, 40, 40), center=False):
    if center:
        (tw, _), _ = cv2.getTextSize(s, FONT, scale, thick)
        x = int(x - tw / 2)
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)


def grid_canvas(mons: List[Mon]) -> np.ndarray:
    rows = (len(mons) + 2) // 3
    canvas = np.full((rows * PITCH + 40, W, 3), 245, np.uint8)
    for i, m in enumerate(mons):
        r, c = divmod(i, 3)
        x0, y0 = c * 240, r * PITCH + 20
        cx = x0 + 120
        _text(canvas, "CP", cx - 70, y0 + 40, 0.7, 2)
        _text(canvas, str(m.cp), cx - 25, y0 + 40, 1.0, 2)
        hue = (hash(m.species_id) % 180)
        col = cv2.cvtColor(np.uint8([[[hue, 160, 200]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        cv2.circle(canvas, (cx, y0 + 140), 62, col, -1)
        _text(canvas, m.name, cx, y0 + 255, 0.9, 2, center=True)
    return canvas


def grid_frame(canvas: np.ndarray, offset: int) -> np.ndarray:
    img = np.full((H, W, 3), 250, np.uint8)
    _text(img, "POKEMON 15/300", 40, 90, 1.1, 2)
    _text(img, "SYNTHETIC TEST VIDEO", 40, 150, 0.6, 1, (120, 120, 120))
    view = canvas[offset: offset + (BOTTOM - TOP)]
    img[TOP: TOP + view.shape[0]] = view
    cv2.line(img, (0, BOTTOM), (W, BOTTOM), (200, 200, 200), 2)
    return img


def detail_frame(m: Mon, candy_name: str) -> np.ndarray:
    img = np.full((H, W, 3), 252, np.uint8)
    _text(img, "CP", 250, 160, 1.0, 2)
    _text(img, str(m.cp), 320, 160, 1.6, 3)
    cv2.circle(img, (W // 2, 420), 150, (90, 170, 90), -1)
    _text(img, m.name, W // 2, 690, 1.6, 3, center=True)
    _text(img, f"{m.hp} / {m.hp} HP", W // 2, 760, 0.9, 2, center=True)
    _text(img, "WEIGHT", 90, 900, 0.7, 2)
    _text(img, "HEIGHT", 520, 900, 0.7, 2)
    _text(img, "STARDUST", 100, 1050, 0.8, 2)
    _text(img, f"{candy_name.upper()} CANDY", 400, 1050, 0.7, 2)
    _text(img, "POWER UP", 260, 1200, 1.0, 2)
    _text(img, "SYNTHETIC TEST VIDEO", 40, 1500, 0.6, 1, (120, 120, 120))
    return img


def appraisal_frame(m: Mon) -> np.ndarray:
    img = np.full((H, W, 3), 252, np.uint8)
    _text(img, "CP", 250, 160, 1.0, 2)
    _text(img, str(m.cp), 320, 160, 1.6, 3)
    cv2.circle(img, (W // 2, 420), 150, (90, 170, 90), -1)
    cv2.rectangle(img, (0, 900), (W, H), (255, 255, 255), -1)
    for i, (label, iv) in enumerate(zip(("Attack", "Defense", "HP"), m.ivs)):
        y = 1020 + i * 150
        _text(img, label, 110, y, 0.9, 2)
        draw_bar(img, 110, y + 25, 250, 14, iv)  # real panel: ~0.35 x width
    return img


def draw_bar(img, x, y, length, thick, iv):
    cv2.rectangle(img, (x, y), (x + length, y + thick), (205, 205, 205), -1)
    fill = int(round(length * iv / 15.0))
    color = (110, 114, 232) if iv == 15 else (61, 159, 247)
    if fill > 0:
        cv2.rectangle(img, (x, y), (x + fill - 1, y + thick), color, -1)
    for k in (1, 2):
        sx = x + int(length * k / 3)
        cv2.rectangle(img, (sx - 2, y), (sx + 1, y + thick), (255, 255, 255), -1)


def write_video(path: Path, mons: Optional[List[Mon]] = None, fps: int = 15, include_detail: bool = True) -> List[Mon]:
    mons = mons or DEFAULT_GRID
    canvas = grid_canvas(mons)
    max_off = canvas.shape[0] - (BOTTOM - TOP)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))

    def hold(img, secs):
        for _ in range(int(fps * secs)):
            vw.write(img)

    off = 0
    hold(grid_frame(canvas, 0), 1.2)
    for target in (430, 860, max_off):
        target = min(target, max_off)
        steps = int(fps * 0.8)
        start = off
        for s in range(1, steps + 1):
            off = int(start + (target - start) * s / steps)
            vw.write(grid_frame(canvas, off))
        hold(grid_frame(canvas, off), 1.2)
    if include_detail:
        hold(detail_frame(mons[0], "Bulbasaur"), 1.5)
        hold(appraisal_frame(mons[0]), 1.5)
    vw.release()
    return mons
