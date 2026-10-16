from pathlib import Path

import pytest

from platewatch.contracts import BoundingBox, PlateRecord
from platewatch.core.store import PlateStore
from platewatch.core.text import normalize_plate, tag_plate
from platewatch.demo import SAMPLES, make_image
from platewatch.pipeline import PlatePipeline
from platewatch.vision.scripted import ScriptedVision, pixel_key


def test_normalize_and_tags():
    assert normalize_plate("abc-12 3") == "ABC123"
    assert "needs-review" not in tag_plate("ABC123", 0.9)
    assert "needs-review" in tag_plate("ABC123", 0.2)
    assert "needs-review" in tag_plate("K9", 0.9)


def _rec(pid: str, text: str) -> PlateRecord:
    return PlateRecord(
        id=pid,
        image_sha256="s",
        source="a.png",
        box=BoundingBox(x_min=0, y_min=0, x_max=5, y_max=5),
        raw_text=text,
        plate_text=text,
        detection_confidence=1,
        ocr_confidence=1,
        confidence=1,
        tags=[],
    )


def test_store_idempotent_and_fuzzy():
    s = PlateStore()
    assert s.add(_rec("1", "ABC1234")) and not s.add(_rec("1", "ABC1234"))
    s.add(_rec("2", "XYZ908I"))
    hits = s.search("xyz 9081")
    assert hits[0].plate_id == "2" and s.search("ABC-1234")[0].exact
    assert s.search("") == []


def _pipeline(tmp_path: Path):
    plans = {}
    for i, (name, drawn, read, dc, oc) in enumerate(SAMPLES):
        img, box = make_image(tmp_path / name, drawn, i)
        plans[pixel_key(img.convert("RGB"))] = [(box, dc, read, oc)]
    v = ScriptedVision(plans)
    store = PlateStore()
    return PlatePipeline(v, v, store), store


def test_scan_is_idempotent_and_skips_bad_files(tmp_path):
    pipe, store = _pipeline(tmp_path)
    (tmp_path / "broken.png").write_bytes(b"not an image")
    first = pipe.scan_inbox(tmp_path)
    assert (first.images_new, first.images_failed, first.plates_stored) == (3, 1, 3)
    assert pipe.scan_inbox(tmp_path).images_new == 0 and store.count() == 3
    assert len(store.needs_review()) == 1


def test_agent_mock_is_grounded(tmp_path):
    from platewatch.agent import PlateDeps, build_agent, mock_model

    pipe, store = _pipeline(tmp_path)
    out = (
        build_agent(mock_model())
        .run_sync("find plate XYZ 9081", deps=PlateDeps(pipe, store, tmp_path))
        .output
    )
    assert out.matches[0].plate_text == "XYZ908I" and out.scanned.plates_stored == 3


def test_agent_rejects_invented_ids(tmp_path):
    from pydantic_ai.models.test import TestModel

    from platewatch.agent import PlateDeps, build_agent

    pipe, store = _pipeline(tmp_path)
    agent = build_agent(
        TestModel(call_tools=[], custom_output_args={"summary": "x", "needs_review": ["made-up"]})
    )
    with pytest.raises(Exception, match=r"retries|Unknown"):
        agent.run_sync("hi", deps=PlateDeps(pipe, store, tmp_path))


def test_core_does_not_import_frameworks():
    import ast

    banned = {"pydantic_ai", "transformers", "torch", "google"}
    for p in Path("platewatch/core").glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not {n.split(".")[0] for n in names} & banned, p
