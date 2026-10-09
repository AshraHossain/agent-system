from datetime import timedelta

import pytest

from opspilot.contracts.request import TimeWindow
from opspilot.core.errors import InvalidArgument, NotFound
from opspilot.core.telemetry import compare_to_baseline, get_telemetry, summarize_anomalies


def test_congestion_detected_with_explicit_rule(ds_factory, windows):
    ds = ds_factory("C02")
    w, b = windows(ds)
    r = compare_to_baseline(ds, "lnk-l1-s1", "utilization_pct", w, b)
    assert r.verdict == "critical"
    assert "critical if >= 90" in r.rule
    assert r.window.count == r.window.expected_count == 24
    assert r.evidence[0].data["verdict"] == "critical"


def test_normal_metric(ds_factory, windows):
    ds = ds_factory("C02")
    r = compare_to_baseline(ds, "lnk-l1-s1", "error_rate", *windows(ds))
    assert r.verdict == "normal"


def test_normal_operations_have_no_anomalies(ds_factory, windows):
    ds = ds_factory("C01")
    s = summarize_anomalies(ds, *windows(ds))
    assert s.anomalies == [] and s.data_quality == []
    assert s.normal_metrics_checked > 100


def test_missing_and_delayed_data_reported(ds_factory, windows):
    ds = ds_factory("C06")
    s = summarize_anomalies(ds, *windows(ds))
    issues = {(d.entity_id, d.metric, d.issue) for d in s.data_quality}
    assert ("lnk-l2-s1", "error_rate", "missing") in issues
    assert ("spine-1", None, "delayed") in issues
    r = compare_to_baseline(ds, "lnk-l2-s1", "error_rate", *windows(ds))
    assert r.verdict == "insufficient_data"
    assert r.evidence[0].kind.value == "DQ"


def test_scoped_summary_and_unknown_entities(ds_factory, windows):
    ds = ds_factory("C03")
    s = summarize_anomalies(ds, *windows(ds), entity_ids=["lnk-l3-s2", "nonexistent-1"])
    assert {a.metric for a in s.anomalies} == {"error_rate", "packet_loss_pct", "probe_loss_pct"}
    assert any(d.issue == "unknown_entity" for d in s.data_quality)


def test_argument_validation(ds_factory, windows):
    ds = ds_factory("C02")
    w, b = windows(ds)
    with pytest.raises(NotFound):
        compare_to_baseline(ds, "nope", "cpu_pct", w, b)
    with pytest.raises(NotFound):
        compare_to_baseline(ds, "fw-1", "utilization_pct", w, b)
    with pytest.raises(InvalidArgument):
        compare_to_baseline(ds, "fw-1", "cpu_pct", TimeWindow(start=w.end, end=w.start), b)
    with pytest.raises(InvalidArgument):
        get_telemetry(ds, "fw-1", None, TimeWindow(start=w.end - timedelta(days=2), end=w.end))
    with pytest.raises(InvalidArgument):
        get_telemetry(ds, "fw-1", ["bogus"], w)


def test_snapshot(ds_factory, windows):
    ds = ds_factory("C04")
    snap = get_telemetry(ds, "fw-1", ["cpu_pct"], windows(ds)[0])
    assert snap.metrics["cpu_pct"].max > 90
    assert snap.evidence[0].evidence_id.startswith("EV-TEL-")


def test_telemetry_unavailable_raises(ds_factory, windows):
    from opspilot.core.errors import DataUnavailable

    ds = ds_factory("C02")

    class Broken(type(ds)):
        def telemetry(self, *a, **k):
            raise DataUnavailable("collector down")

    with pytest.raises(DataUnavailable):
        summarize_anomalies(Broken(ds.path, ds.dataset_id), *windows(ds))
