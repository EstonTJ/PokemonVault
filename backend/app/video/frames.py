"""FFmpeg-based probing, sampling and keyframe selection.

FFmpeg decodes and scales (to a normalized width) at a reduced frame rate, so
Python never touches every frame. Keyframes are chosen from motion and scroll
signals:

* a frame is kept once motion settles after a change, and it differs from the
  previous keyframe;
* while scrolling slowly and continuously, a frame is kept every ~half screen of
  scroll so no grid row is skipped;
* frames are tagged with a segment number that increments whenever scroll
  tracking is lost (e.g. a screen change), so grouping never trusts offsets
  across a discontinuity.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional

import cv2
import numpy as np

from ..config import settings


def ffmpeg_exe() -> str:
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    rotation: int


class VideoError(Exception):
    pass


def probe(path: Path) -> VideoInfo:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60)
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    v = re.search(r"Stream #\S+.*?Video:.*?(\d{2,5})x(\d{2,5})", err)
    if not m or not v:
        raise VideoError("Not a readable video (no duration or video stream found).")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    w, h = int(v.group(1)), int(v.group(2))
    fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    rot_m = re.search(r"rotation of\s*(-?\d+(?:\.\d+)?)", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    rot = int(float(rot_m.group(1))) % 360 if rot_m else 0
    if rot in (90, 270):
        w, h = h, w  # ffmpeg auto-rotates on decode
    return VideoInfo(dur, w, h, float(fps_m.group(1)) if fps_m else 30.0, rot)


@dataclass
class SampledFrame:
    index: int
    timestamp_ms: int
    image: np.ndarray  # BGR, normalized width


def sample_frames(path: Path, info: VideoInfo, fps: float, width: int) -> Iterator[SampledFrame]:
    out_h = int(round(info.height * width / info.width / 2.0)) * 2
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vf", f"fps={fps},scale={width}:{out_h}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    frame_bytes = width * out_h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        i = 0
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            img = np.frombuffer(buf, np.uint8).reshape(out_h, width, 3)
            yield SampledFrame(i, int(round(i * 1000.0 / fps)), img)
            i += 1
    finally:
        proc.kill()
        proc.wait()


# ----------------------------------------------------------------- motion & scroll

@dataclass
class Keyframe:
    index: int
    timestamp_ms: int
    image: np.ndarray
    segment: int
    scroll_offset: float


class KeyframeSelector:
    """Streaming keyframe selection with scroll tracking (pure, testable)."""

    STABLE = 2.5          # mean abs diff (0..255) below which the screen is "still"
    CHANGE = 6.0          # min diff vs last keyframe for a new still keyframe
    TRACK_MIN_RESP = 0.08  # phase-correlation response below this means tracking is lost

    def __init__(self, band=(0.14, 0.90)):
        self.band = band
        self.prev_small: Optional[np.ndarray] = None
        self.prev_band: Optional[np.ndarray] = None
        self.last_kf_small: Optional[np.ndarray] = None
        self.last_kf_offset = 0.0
        self.offset = 0.0
        self.segment = 0
        self.moving = False
        self._window = None

    def _prep(self, img: np.ndarray):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (gray.shape[1] // 4, gray.shape[0] // 4), interpolation=cv2.INTER_AREA).astype(np.float32)
        h = gray.shape[0]
        band = gray[int(h * self.band[0]) : int(h * self.band[1])].astype(np.float32)
        return small, band

    @staticmethod
    def _band_diff(a: np.ndarray, b: np.ndarray, bands: int = 8) -> float:
        """Max mean-abs-diff over horizontal bands: sensitive to changes confined to part of the screen (overlays)."""
        d = np.abs(a - b)
        hs = np.array_split(d, bands, axis=0)
        return float(max(x.mean() for x in hs))

    def push(self, f: SampledFrame) -> Optional[Keyframe]:
        small, band = self._prep(f.image)
        if self.prev_small is None:
            self.prev_small, self.prev_band = small, band
            self.moving = True  # treat the first stable frame as a keyframe
            return None
        motion = float(np.mean(np.abs(small - self.prev_small)))
        if motion > 0.4:
            if self._window is None or self._window.shape != band.shape[::-1]:
                self._window = cv2.createHanningWindow((band.shape[1], band.shape[0]), cv2.CV_32F)
            (dx, dy), resp = cv2.phaseCorrelate(self.prev_band, band, self._window)
            vertical = abs(dy) > 1.0 and abs(dx) < max(4.0, abs(dy) * 0.25)
            if vertical and resp >= self.TRACK_MIN_RESP:
                self.offset += -dy  # content moves up when scrolling down
            elif motion > self.CHANGE:
                # Large non-scroll change (screen switch, swipe to another Pokémon): new segment.
                self.segment += 1
                self.offset = 0.0
                self.last_kf_offset = 0.0
                self.last_kf_small = None
        self.prev_small, self.prev_band = small, band

        kf = None
        if motion < self.STABLE:
            if self.moving or self.last_kf_small is None:
                diff = 255.0 if self.last_kf_small is None else self._band_diff(small, self.last_kf_small)
                if diff > self.CHANGE or abs(self.offset - self.last_kf_offset) > band.shape[0] * 0.2:
                    kf = Keyframe(f.index, f.timestamp_ms, f.image.copy(), self.segment, self.offset)
            self.moving = False
        else:
            self.moving = True
            if abs(self.offset - self.last_kf_offset) > band.shape[0] * 0.5 and motion < self.CHANGE * 2:
                kf = Keyframe(f.index, f.timestamp_ms, f.image.copy(), self.segment, self.offset)
        if kf is not None:
            self.last_kf_small = small
            self.last_kf_offset = self.offset
        return kf


def extract_keyframes(path: Path, info: VideoInfo, on_progress: Callable[[float], None]) -> List[Keyframe]:
    sel = KeyframeSelector()
    out: List[Keyframe] = []
    total = max(1, int(info.duration_s * settings.sample_fps))
    for f in sample_frames(path, info, settings.sample_fps, settings.normalized_width):
        kf = sel.push(f)
        if kf is not None:
            out.append(kf)
            if len(out) >= settings.max_keyframes:
                break
        if f.index % 8 == 0:
            on_progress(min(1.0, f.index / total))
    return out
