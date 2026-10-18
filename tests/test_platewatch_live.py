"""Live check of the Hugging Face detector + TrOCR on real photos. Excluded by default.

    PLATEWATCH_LIVE_DIR=/path/to/photos uv run --with transformers --with torch pytest -m live \
        tests/test_platewatch_live.py -s

Each photo is named after the plate it shows, e.g. ``ABC1234.jpg`` or ``ABC1234_2.jpg``. Needs
network access to download the models. Thresholds are deliberately loose: real models are
nondeterministic across hardware/versions, so this guards against a broken integration, not
against a specific accuracy.
"""

import os
import re
from pathlib import Path

import pytest
from platewatch.core.store import PlateStore
from platewatch.core.text import normalize_plate
from platewatch.pipeline import IMAGE_SUFFIXES, PlatePipeline

pytestmark = pytest.mark.live

MIN_DETECTED = 0.8  # share of photos where at least one plate is found
MIN_RECALL_AT_3 = 0.5  # share of photos whose true plate is in the top-3 fuzzy hits


@pytest.fixture(scope="module")
def photos() -> list[Path]:
    root = os.getenv("PLATEWATCH_LIVE_DIR")
    if not root or not Path(root).is_dir():
        pytest.skip("set PLATEWATCH_LIVE_DIR to a folder of photos named after their plates")
    files = sorted(p for p in Path(root).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        pytest.skip("PLATEWATCH_LIVE_DIR has no images")
    return files


def _expected(path: Path) -> str:
    return normalize_plate(re.sub(r"_\d+$", "", path.stem))


def test_real_models_detect_read_and_search(photos):
    pytest.importorskip("transformers")
    pytest.importorskip("torch")
    from platewatch.vision.huggingface import HFPlateDetector, HFPlateReader

    store = PlateStore()
    pipe = PlatePipeline(HFPlateDetector(), HFPlateReader(), store)
    found = recalled = exact = 0
    for path in photos:
        recs = pipe.process_file(path, path.name)
        for r in recs:
            store.add(r)
        want = _expected(path)
        got = [r.plate_text for r in recs]
        top3 = [h.plate_text for h in store.search(want, limit=3)]
        found += bool(recs)
        exact += want in got
        recalled += want in top3
        print(f"{path.name:28} want={want:10} read={got}")
    n = len(photos)
    print(f"detected {found}/{n}  exact {exact}/{n}  recall@3 {recalled}/{n}")
    assert found / n >= MIN_DETECTED
    assert recalled / n >= MIN_RECALL_AT_3
