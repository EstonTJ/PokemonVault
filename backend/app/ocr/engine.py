"""Local OCR engines behind one interface.

Preference order:
  1. PaddleOCR (if `paddleocr` is installed)
  2. RapidOCR (PaddleOCR's detection and recognition models running on ONNX Runtime; lighter install)

Nothing is ever sent to a remote service.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import List, Optional, Protocol

import numpy as np

log = logging.getLogger("pokevault.ocr")


@dataclass
class OcrToken:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    def to_json(self) -> dict:
        return {"text": self.text, "conf": round(self.conf, 4), "box": [round(self.x0, 1), round(self.y0, 1), round(self.x1, 1), round(self.y1, 1)]}

    @classmethod
    def from_json(cls, d: dict) -> "OcrToken":
        x0, y0, x1, y1 = d["box"]
        return cls(d["text"], d["conf"], x0, y0, x1, y1)


class OcrEngine(Protocol):
    name: str

    def read(self, image_bgr: np.ndarray) -> List[OcrToken]: ...


def _quad_to_box(quad) -> tuple:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


class RapidOcrEngine:
    name = "rapidocr-onnx (PaddleOCR models)"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()
        self._lock = threading.Lock()

    def read(self, image_bgr: np.ndarray) -> List[OcrToken]:
        with self._lock:
            result, _ = self._ocr(image_bgr)
        tokens: List[OcrToken] = []
        for quad, text, conf in result or []:
            x0, y0, x1, y1 = _quad_to_box(quad)
            tokens.append(OcrToken(str(text).strip(), float(conf), x0, y0, x1, y1))
        return [t for t in tokens if t.text]


class PaddleOcrEngine:
    name = "paddleocr"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR  # type: ignore

        self._ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
        self._lock = threading.Lock()

    def read(self, image_bgr: np.ndarray) -> List[OcrToken]:
        with self._lock:
            result = self._ocr.ocr(image_bgr, cls=False)
        tokens: List[OcrToken] = []
        for line in (result or [[]])[0] or []:
            quad, (text, conf) = line
            x0, y0, x1, y1 = _quad_to_box(quad)
            tokens.append(OcrToken(str(text).strip(), float(conf), x0, y0, x1, y1))
        return [t for t in tokens if t.text]


_engine: Optional[OcrEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> OcrEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            for cls in (PaddleOcrEngine, RapidOcrEngine):
                try:
                    _engine = cls()
                    log.info("OCR engine: %s", cls.name)
                    break
                except Exception as exc:  # noqa: BLE001
                    log.info("OCR engine %s unavailable: %s", cls.name, exc)
            if _engine is None:
                raise RuntimeError("No local OCR engine available. Install rapidocr_onnxruntime or paddleocr.")
        return _engine


def set_engine(engine: Optional[OcrEngine]) -> None:
    """Swap the engine (tests use a fake engine)."""
    global _engine
    with _engine_lock:
        _engine = engine
