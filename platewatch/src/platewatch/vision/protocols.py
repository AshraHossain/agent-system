from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PIL import Image

    from platewatch.contracts import BoundingBox, RawDetection


class Detector(Protocol):
    def detect(self, image: Image.Image) -> list[RawDetection]: ...


class Reader(Protocol):
    def read(self, image: Image.Image, box: BoundingBox) -> tuple[str, float]:
        """Return (raw_text, confidence in [0, 1]) for the plate inside ``box``."""
        ...
