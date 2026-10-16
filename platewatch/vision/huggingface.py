"""Hugging Face backends (object-detection pipeline + TrOCR). Heavy deps are imported lazily.

Install on demand (not in the lockfile, they pull in torch):
    uv run --with transformers --with torch platewatch ingest --backend hf <dir>

Model ids are defaults, overridable via PLATEWATCH_DETECTOR_MODEL / PLATEWATCH_OCR_MODEL. They have
NOT been exercised in CI (offline); see tests/test_platewatch_live.py.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from platewatch.contracts import BoundingBox, RawDetection

if TYPE_CHECKING:
    from PIL import Image

DEFAULT_DETECTOR = "nickmuchi/yolos-small-finetuned-license-plate-detection"
DEFAULT_OCR = "microsoft/trocr-base-printed"
MIN_DETECTION_SCORE = 0.3


class HFPlateDetector:
    def __init__(self, model: str | None = None) -> None:
        from transformers import pipeline

        self._pipe = pipeline(
            "object-detection",
            model=model or os.getenv("PLATEWATCH_DETECTOR_MODEL", DEFAULT_DETECTOR),
        )

    def detect(self, image: Image.Image) -> list[RawDetection]:
        out = []
        for d in self._pipe(image.convert("RGB")):
            if d["score"] < MIN_DETECTION_SCORE:
                continue
            b = d["box"]
            x0, y0 = max(0, int(b["xmin"])), max(0, int(b["ymin"]))
            x1, y1 = int(b["xmax"]), int(b["ymax"])
            if x1 > x0 and y1 > y0:
                out.append(
                    RawDetection(
                        box=BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1),
                        confidence=float(d["score"]),
                    )
                )
        return out


class HFPlateReader:
    def __init__(self, model: str | None = None) -> None:
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        name = model or os.getenv("PLATEWATCH_OCR_MODEL", DEFAULT_OCR)
        self._torch = torch
        self._proc = TrOCRProcessor.from_pretrained(name)
        self._model = VisionEncoderDecoderModel.from_pretrained(name).eval()

    def read(self, image: Image.Image, box: BoundingBox) -> tuple[str, float]:
        crop = image.convert("RGB").crop(box.as_tuple())
        pixels = self._proc(images=crop, return_tensors="pt").pixel_values
        with self._torch.no_grad():
            gen = self._model.generate(
                pixels, max_new_tokens=16, output_scores=True, return_dict_in_generate=True
            )
        text = self._proc.batch_decode(gen.sequences, skip_special_tokens=True)[0]
        # Mean token probability of the chosen tokens as a rough confidence signal.
        scores = self._model.compute_transition_scores(
            gen.sequences, gen.scores, normalize_logits=True
        )
        conf = float(self._torch.exp(scores[0]).mean()) if scores.numel() else 0.0
        return text, max(0.0, min(1.0, conf))
