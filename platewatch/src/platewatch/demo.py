"""Offline demo: synthetic 'photos' with drawn plates, scripted detector/OCR, mock agent."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from platewatch.agent import PlateDeps, build_agent, mock_model
from platewatch.contracts import BoundingBox
from platewatch.core.store import PlateStore
from platewatch.pipeline import PlatePipeline
from platewatch.vision.scripted import Plan, ScriptedVision, pixel_key

# (file, plate text as drawn, OCR text as read, det_conf, ocr_conf)
SAMPLES = [
    ("car1.png", "ABC 1234", "ABC 1234", 0.97, 0.95),
    ("car2.png", "XYZ 9081", "XYZ 908I", 0.92, 0.81),  # OCR slip: 1 -> I
    ("car3.png", "K9", "K9", 0.60, 0.40),  # short + low confidence -> needs-review
]


def make_image(path: Path, text: str, seed: int) -> tuple[Image.Image, BoundingBox]:
    img = Image.new("RGB", (320, 200), (40 + seed * 20, 60, 90))
    d = ImageDraw.Draw(img)
    box = BoundingBox(x_min=60, y_min=120, x_max=260, y_max=170)
    d.rectangle(box.as_tuple(), fill=(240, 240, 240), outline=(0, 0, 0))
    d.text((80, 138), text, fill=(0, 0, 0))
    img.save(path)
    return Image.open(path), box


def run_demo() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp)
        plans: dict[str, Plan] = {}
        for i, (name, drawn, read, dc, oc) in enumerate(SAMPLES):
            img, box = make_image(inbox / name, drawn, i)
            plans[pixel_key(img.convert("RGB"))] = [(box, dc, read, oc)]
        store = PlateStore()
        vision = ScriptedVision(plans)
        deps = PlateDeps(PlatePipeline(vision, vision, store), store, inbox)
        out = build_agent(mock_model()).run_sync("find plate XYZ 9081", deps=deps).output
        print(out.model_dump_json(indent=2))
    return 0
