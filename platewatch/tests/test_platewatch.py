import contextlib
from collections.abc import Iterator
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


@pytest.fixture
def store() -> Iterator[PlateStore]:
    """An in-memory store that is closed afterwards.

    PlateStore holds a long-lived connection, so an abandoned one raises
    "ResourceWarning: unclosed database" when the collector reaches it.
    """
    with PlateStore() as s:
        yield s


def test_store_idempotent_and_fuzzy(store: PlateStore):
    assert store.add(_rec("1", "ABC1234")) and not store.add(_rec("1", "ABC1234"))
    store.add(_rec("2", "XYZ908I"))
    hits = store.search("xyz 9081")
    assert hits[0].plate_id == "2" and store.search("ABC-1234")[0].exact
    assert store.search("") == []


@contextlib.contextmanager
def _pipeline(tmp_path: Path):
    """Yield a pipeline and its store, closing the store on exit.

    PlateStore holds a long-lived connection; an abandoned one raises
    "ResourceWarning: unclosed database" whenever the collector reaches it.
    """
    plans = {}
    for i, (name, drawn, read, dc, oc) in enumerate(SAMPLES):
        img, box = make_image(tmp_path / name, drawn, i)
        plans[pixel_key(img.convert("RGB"))] = [(box, dc, read, oc)]
    v = ScriptedVision(plans)
    with PlateStore() as store:
        yield PlatePipeline(v, v, store), store


def test_scan_is_idempotent_and_skips_bad_files(tmp_path):
    with _pipeline(tmp_path) as (pipe, store):
        (tmp_path / "broken.png").write_bytes(b"not an image")
        first = pipe.scan_inbox(tmp_path)
        assert (first.images_new, first.images_failed, first.plates_stored) == (3, 1, 3)
        assert pipe.scan_inbox(tmp_path).images_new == 0 and store.count() == 3
        assert len(store.needs_review()) == 1


def test_agent_mock_is_grounded(tmp_path):
    from platewatch.agent import PlateDeps, build_agent, mock_model

    with _pipeline(tmp_path) as (pipe, store):
        out = (
            build_agent(mock_model())
            .run_sync("find plate XYZ 9081", deps=PlateDeps(pipe, store, tmp_path))
            .output
        )
    assert out.matches[0].plate_text == "XYZ908I" and out.scanned.plates_stored == 3


def test_agent_rejects_invented_ids(tmp_path):
    from pydantic_ai.models.test import TestModel

    from platewatch.agent import PlateDeps, build_agent

    with _pipeline(tmp_path) as (pipe, store):
        agent = build_agent(
            TestModel(
                call_tools=[], custom_output_args={"summary": "x", "needs_review": ["made-up"]}
            )
        )
        with pytest.raises(Exception, match=r"retries|Unknown"):
            agent.run_sync("hi", deps=PlateDeps(pipe, store, tmp_path))


def test_core_does_not_import_frameworks():
    import ast

    import platewatch.core

    banned = {"pydantic_ai", "transformers", "torch", "google"}
    files = list(Path(platewatch.core.__file__).parent.glob("*.py"))
    assert files
    for p in files:
        for node in ast.walk(ast.parse(p.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not {n.split(".")[0] for n in names} & banned, p


def test_cli_demo_and_search(tmp_path, capsys):
    from platewatch import cli

    assert cli.main(["demo"]) == 0
    assert "XYZ908I" in capsys.readouterr().out
    db = tmp_path / "p.db"
    store = PlateStore(db)
    store.add(_rec("1", "ABC1234"))
    store.close()
    assert cli.main(["--db", str(db), "search", "abc 1234"]) == 0
    assert '"exact":true' in capsys.readouterr().out.replace(" ", "")


def test_cli_ingest_and_ask_use_backend(tmp_path, monkeypatch, capsys):
    from platewatch import cli

    with _pipeline(tmp_path) as (pipe, _):
        vision = pipe.detector
    monkeypatch.setattr(cli, "_backend", lambda name: (vision, vision))
    db = tmp_path / "p.db"
    assert cli.main(["--db", str(db), "ingest", str(tmp_path)]) == 0
    assert '"plates_stored": 3' in capsys.readouterr().out
    assert cli.main(["--db", str(db), "ask", "find plate XYZ 9081", "--inbox", str(tmp_path)]) == 0
    assert "XYZ908I" in capsys.readouterr().out


def test_cli_rejects_unknown_backend():
    from platewatch import cli

    with pytest.raises(SystemExit):
        cli._backend("scripted")


# ---------------------------------------------------------------------------
# Hardening tests
# ---------------------------------------------------------------------------


def _failing_reader(image, box):
    """Simulates an OCR crash on the first box."""
    raise RuntimeError("OCR engine crashed")


def test_reader_error_skips_detection_not_image(tmp_path, store: PlateStore):
    """A reader exception on one crop skips that crop but keeps the others."""
    from platewatch.vision.scripted import ScriptedVision

    # One good detection with a working reader.
    img, box = make_image(tmp_path / "good.png", "ABC123", 0)
    plans = {pixel_key(img.convert("RGB")): [(box, 0.9, "ABC123", 0.95)]}
    real_vision = ScriptedVision(plans)

    # Monkey-patch reader to fail once then succeed.
    call_count = [0]

    def flaky_reader(image, box):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("first read failed")
        return real_vision.read(image, box)

    from platewatch.pipeline import PlatePipeline

    class PatchedReader:
        def read(self, image, box):
            return flaky_reader(image, box)

    pipe = PlatePipeline(real_vision, PatchedReader(), store)
    # With one failing reader, process_file should return empty (skipped)
    records = pipe.process_file(tmp_path / "good.png", "good.png")
    assert records == []
    assert call_count[0] == 1


def test_image_timeout_recorded_as_failure(tmp_path, monkeypatch, store: PlateStore):
    """When process_file times out, scan_inbox records it as a failure, not a crash."""
    import platewatch.pipeline as plmod
    from platewatch.pipeline import PlatePipeline
    from platewatch.vision.scripted import ScriptedVision

    img, box = make_image(tmp_path / "slow.png", "ABC123", 0)
    plans = {pixel_key(img.convert("RGB")): [(box, 0.9, "ABC123", 0.95)]}
    vision = ScriptedVision(plans)

    original_process = PlatePipeline.process_file

    def hanging_process(self, path, source):
        import time

        time.sleep(999)  # will be killed by timeout
        return original_process(self, path, source)

    monkeypatch.setattr(PlatePipeline, "process_file", hanging_process)
    monkeypatch.setattr(plmod, "IMAGE_TIMEOUT", 0.1)

    pipe = PlatePipeline(vision, vision, store)
    summary = pipe.scan_inbox(tmp_path)
    assert summary.images_failed == 1
    assert "TimeoutError" in summary.failures[0]
    assert store.count() == 0


def test_sightings_count_tracks_duplicate_plate_text(tmp_path, store: PlateStore):
    """Same plate text from two different images gets sightings=2 in search results."""
    # two distinct records (different ids, same plate_text)
    store.add(_rec("id-1", "ABC1234"))
    store.add(
        PlateRecord(
            id="id-2",
            image_sha256="different-sha",
            source="b.png",
            box=BoundingBox(x_min=0, y_min=0, x_max=5, y_max=5),
            raw_text="ABC1234",
            plate_text="ABC1234",
            detection_confidence=0.8,
            ocr_confidence=0.8,
            confidence=0.64,
            tags=[],
        )
    )
    hits = store.search("ABC1234")
    assert all(h.sightings == 2 for h in hits if h.plate_text == "ABC1234")
    assert len(hits) == 2
