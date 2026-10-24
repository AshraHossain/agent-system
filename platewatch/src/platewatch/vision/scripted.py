"""Deterministic offline backend: detections/readings are scripted per image.

Mirrors the repo's mock-model idea. It proves the pipeline, storage, search and agent work; it says
nothing about real detection/OCR accuracy.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from platewatch.contracts import BoundingBox, RawDetection

if TYPE_CHECKING:
    from PIL import Image

Plan = list[tuple[BoundingBox, float, str, float]]  # box, det_conf, text, ocr_conf


def pixel_key(image: Image.Image) -> str:
    return hashlib.sha1(image.convert("RGB").tobytes(), usedforsecurity=False).hexdigest()


class ScriptedVision:
    """Acts as both Detector and Reader."""

    def __init__(self, plans: dict[str, Plan]) -> None:
        self._plans = plans

    def detect(self, image: Image.Image) -> list[RawDetection]:
        return [
            RawDetection(box=b, confidence=dc)
            for b, dc, _, _ in self._plans.get(pixel_key(image), [])
        ]

    def read(self, image: Image.Image, box: BoundingBox) -> tuple[str, float]:
        for b, _, text, oc in self._plans.get(pixel_key(image), []):
            if b == box:
                return text, oc
        return "", 0.0
