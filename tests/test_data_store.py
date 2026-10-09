"""Phase 5: investigator-visible data access, coverage reporting, evidence ids."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from netpulse.data.schemas import TelemetryQuery
from netpulse.data.store import DEFAULT_DATA_DIR, DatasetStore, coverage_evidence
from netpulse.errors import DataCorruptError, DataNotFoundError, ToolInputError
from netpulse.evidence import EvidenceIdAllocator
from netpulse.models import EvidenceSource, Metric

ROOT = Path(__file__).resolve().parent.parent
CASES = {c["case_id"]: c for c in map(json.loads, (ROOT / "eval/datasets/v1/cases.jsonl").read_text().splitlines())}
# Tests may read labels to pick cases; netpulse code may not.
SCENARIO_CASE = {
    lab["scenario"]: lab["case_id"]
    for lab in map(json.loads, (ROOT / "eval/labels/v1/labels.jsonl").read_text().splitlines())
}


@pytest.fixture(scope="module")
def store() -> DatasetStore:
    return DatasetStore(DEFAULT_DATA_DIR)


def query_for(scenario: str, **overrides) -> TelemetryQuery:
    case = CASES[SCENARIO_CASE[scenario]]
    sub = case["submission"]
    params = dict(
        dataset_id=sub["dataset_id"],
        window_start=sub["window_start"],
        window_end=sub["window_end"],
        as_of=case["submitted_at"],
    )
    return TelemetryQuery(**{**params, **overrides})


# --- telemetry window -------------------------------------------------------


def test_window_returns_history_and_window_with_reference_metadata(store):
    q = query_for("normal_quiet")
    w = store.telemetry_window(q)
    assert set(w.frames) == {"links", "nodes", "services"}
    ts = w.frames["links"]["timestamp"]
    assert ts.min() <= pd.Timestamp(q.window_start) - pd.Timedelta(hours=24)
    assert ts.max() <= pd.Timestamp(q.window_end)
    assert w.ref.dataset_id == q.dataset_id and w.ref.entities_without_data == []
    assert w.ref.row_count == w.ref.expected_row_count == 60 * len(w.coverage)


def test_delayed_rows_are_invisible_and_counted_as_pending(store):
    q = query_for("delayed_telemetry")
    w = store.telemetry_window(q)
    _, values = w.series("agg-4", Metric.CPU_PCT)
    timestamps, _ = w.series("agg-4", Metric.CPU_PCT)
    assert timestamps.max() <= pd.Timestamp(q.as_of) - pd.Timedelta(hours=2)
    cov = next(c for c in w.coverage if c.entity_id == "agg-4")
    assert cov.pending_points > 0 and cov.visible_points + cov.pending_points == cov.expected_points
    assert "delayed" in coverage_evidence(cov, "ev-dq-0001").summary


def test_missing_data_is_reported_not_filled(store):
    w = store.telemetry_window(query_for("missing_data"))
    cov = next(c for c in w.coverage if c.entity_id == "link-agg-3-acc-6")
    assert cov.coverage_ratio < 0.5 and cov.pending_points == 0 and cov.gaps
    item = coverage_evidence(cov, "ev-dq-0001")
    assert item.source == EvidenceSource.DATA_QUALITY and "not evidence of health" in item.summary
    times, values = w.series("link-agg-3-acc-6", Metric.PACKET_LOSS_PCT)
    assert not values.isna().any() and times.max() < cov.gaps[-1].start  # no synthetic rows inside the gap


def test_entity_and_metric_filters(store):
    w = store.telemetry_window(query_for("normal_quiet", entity_ids=["core-1"], metrics=[Metric.CPU_PCT]))
    assert list(w.frames) == ["nodes"]
    assert list(w.frames["nodes"].columns) == ["timestamp", "entity_id", "cpu_pct"]
    assert [c.entity_id for c in w.coverage] == ["core-1"]


def test_history_hours_bounds_lookback(store):
    q = query_for("normal_quiet", history_hours=1)
    ts = store.telemetry_window(q).frames["nodes"]["timestamp"]
    assert ts.min() >= pd.Timestamp(q.window_start) - pd.Timedelta(hours=1)


def test_unknown_entity_is_rejected(store):
    with pytest.raises(ToolInputError, match="unknown entities"):
        store.telemetry_window(query_for("normal_quiet", entity_ids=["core-9"]))


@pytest.mark.parametrize("dataset_id", ["../labels", "case-01/../../x", "CASE-01", "case 01", ""])
def test_dataset_id_validation_blocks_traversal(dataset_id):
    with pytest.raises((ValidationError, ToolInputError)):
        q = query_for("normal_quiet").model_copy(update={"dataset_id": dataset_id})
        DatasetStore(DEFAULT_DATA_DIR)._case_dir(q.dataset_id)


def test_missing_dataset_is_not_found(store):
    with pytest.raises(DataNotFoundError):
        store.telemetry_window(query_for("normal_quiet", dataset_id="case-99"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_start": datetime(2026, 3, 2, 9, 0)},
        {"as_of": datetime(2020, 1, 1, tzinfo=UTC)},
        {"history_hours": 500},
    ],
)
def test_query_validation(overrides):
    with pytest.raises(ValidationError):
        query_for("normal_quiet", **overrides)


def test_store_refuses_labels_root_and_missing_topology(tmp_path):
    with pytest.raises(ToolInputError):
        DatasetStore(ROOT / "eval/labels/v1")
    with pytest.raises(DataNotFoundError):
        DatasetStore(tmp_path)


def test_corrupt_table_is_reported(tmp_path):
    case_id = SCENARIO_CASE["normal_quiet"]
    shutil.copy(DEFAULT_DATA_DIR / "topology.json", tmp_path / "topology.json")
    shutil.copytree(DEFAULT_DATA_DIR / "cases" / case_id, tmp_path / "cases" / case_id)
    pd.DataFrame({"timestamp": ["x"], "oops": [1]}).to_csv(tmp_path / "cases" / case_id / "nodes.csv.gz", index=False)
    store = DatasetStore(tmp_path)
    with pytest.raises(DataCorruptError, match="columns"):
        store.telemetry_window(query_for("normal_quiet", metrics=[Metric.CPU_PCT]))


def test_events_respect_window_and_as_of(store):
    case = CASES[SCENARIO_CASE["acl_change_regression"]]
    sub = case["submission"]
    start = datetime.fromisoformat(sub["window_start"])
    events = store.events(
        sub["dataset_id"], start, datetime.fromisoformat(sub["window_end"]), start + timedelta(hours=2)
    )
    assert all(start <= e.timestamp <= start + timedelta(hours=2) for e in events)
    assert any(e.event_type == "config_change" and e.entity_id == "agg-4" for e in events)


def test_maintenance_windows_load(store):
    windows = store.maintenance(SCENARIO_CASE["maintenance_side_effect"])
    assert [w.ticket for w in windows] == ["CHG-4471"]
    assert store.maintenance(SCENARIO_CASE["normal_quiet"]) == []


# --- evidence ids -------------------------------------------------------------


def test_allocator_continues_numbering_from_existing_registry():
    alloc = EvidenceIdAllocator(["ev-anom-0003", "ev-anom-0001", "ev-rb-0002", "not-an-id"])
    assert alloc.next("anom") == "ev-anom-0004"
    assert alloc.next("rb") == "ev-rb-0003"
    assert alloc.next("topo") == "ev-topo-0001"
    with pytest.raises(ValueError):
        alloc.next("bogus")
