"""Phase 7: deterministic evidence verification."""

from datetime import UTC, datetime

import pytest
from graph_helpers import shared_deps

from netpulse.graph.verification import verify
from netpulse.models import Anomaly, EvidenceItem, Hypothesis

T = datetime(2026, 1, 1, tzinfo=UTC)


def ev(eid, source="detector", entities=("core-2",), trusted=True, summary="cpu_pct on core-2 high; peak 97.5 %"):
    return EvidenceItem(evidence_id=eid, source=source, summary=summary, entity_ids=list(entities), trusted=trusted)


REG = {
    e.evidence_id: e
    for e in [
        ev("ev-anom-0001"),
        ev("ev-topo-0001", "topology", ("core-2", "link-core-2-agg-1"), summary="localization"),
        ev("ev-rb-0001", "runbook", (), trusted=False, summary="RB-005 high cpu"),
        ev("ev-anom-0002", entities=("link-core-2-agg-1",), summary="utilization_pct high"),
        ev("ev-mnt-0001", "maintenance", ("link-core-1-core-2",), summary="Planned maintenance CHG-1"),
        ev("ev-evt-0001", "event_log", ("agg-4",), summary="notice config_change on agg-4"),
    ]
}
CPU = Anomaly(
    anomaly_id="a1",
    entity_id="core-2",
    metric="cpu_pct",
    detectors=["robust_zscore", "window_comparison"],
    confirmed=True,
    direction="high",
    start=T,
    end=T,
    peak_value=97.5,
    peak_at=T,
    evidence_id="ev-anom-0001",
)
OBSERVED = ["core-1", "core-2", "agg-4", "link-core-2-agg-1", "link-core-1-core-2"]


def hyp(**kw) -> Hypothesis:
    base = dict(
        hypothesis_id="hyp-01",
        description="Inferred CPU saturation on core-2",
        cause_category="device_cpu_saturation",
        suspected_root_entity="core-2",
        supporting_evidence=["ev-anom-0001", "ev-topo-0001"],
        confidence_rationale="cited detector and topology evidence",
        generated_by="llm",
    )
    return Hypothesis(**{**base, **kw})


def run(hypotheses, anomalies=(CPU,), gaps=(), registry=REG):
    return verify(list(hypotheses), registry, list(anomalies), shared_deps().topology, list(gaps), OBSERVED, 1)


def codes(outcome):
    return {i.code.value for i in outcome.result.issues}


def test_well_grounded_hypothesis_passes():
    out = run([hyp()])
    assert out.result.passed and out.result.accepted_hypothesis_ids == ["hyp-01"] and out.feedback == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"supporting_evidence": ["ev-anom-0001", "ev-anom-9999"]}, "unknown_evidence_ref"),
        ({"contradicting_evidence": ["ev-ghost-0001"]}, "unknown_evidence_ref"),
        ({"supporting_evidence": []}, "no_supporting_evidence"),
        ({"supporting_evidence": ["ev-rb-0001"]}, "untrusted_only_support"),
        ({"suspected_root_entity": "core-9"}, "unknown_entity"),
        ({"affected_components": ["router-x"]}, "unknown_entity"),
        ({"suspected_root_entity": "core-1"}, "entity_not_in_evidence"),
        ({"description": "CPU on core-2 reached 99.9% causing drops"}, "numeric_claim_unsupported"),
        ({"confidence_rationale": "latency rose by 42 ms"}, "numeric_claim_unsupported"),
    ],
)
def test_blocking_issues_reject_hypothesis(overrides, code):
    out = run([hyp(**overrides)])
    assert not out.result.passed and code in codes(out)
    assert out.result.rejected_hypothesis_ids == ["hyp-01"] and out.feedback


def test_numbers_present_in_cited_evidence_and_small_counts_are_allowed():
    out = run([hyp(description="CPU on core-2 peaked at 97.5 % across 2 services")])
    assert out.result.passed


def test_identifiers_with_digits_are_not_numeric_claims():
    out = run([hyp(description="Inferred saturation on core-2 affecting link-core-2-agg-1 per ev-anom-0001")])
    assert out.result.passed


def test_telemetry_contradiction_rejects_and_records_check_evidence():
    out = run([hyp()], anomalies=[])  # core-2 observed, no CPU anomaly
    assert "contradicted_by_telemetry" in codes(out)
    (chk,) = out.new_evidence.values()
    assert chk.evidence_id == "ev-chk-0001" and chk.trusted and "core-2" in chk.entity_ids
    issue = next(i for i in out.result.issues if i.code.value == "contradicted_by_telemetry")
    assert issue.evidence_id == chk.evidence_id


def test_contradiction_check_evidence_is_reused_across_attempts():
    first = run([hyp()], anomalies=[])
    registry = {**REG, **first.new_evidence}
    second = run([hyp()], anomalies=[], registry=registry)
    assert second.new_evidence == {} and "contradicted_by_telemetry" in codes(second)


def test_unobserved_root_cannot_be_contradicted():
    out = run([hyp()], anomalies=[], gaps=["core-2"])
    assert "contradicted_by_telemetry" not in codes(out)


def test_category_kind_mismatch_is_contradiction():
    out = run(
        [
            hyp(
                cause_category="device_memory_exhaustion",
                suspected_root_entity="link-core-2-agg-1",
                supporting_evidence=["ev-anom-0002"],
            )
        ]
    )
    assert "contradicted_by_telemetry" in codes(out)


def test_maintenance_and_config_hypotheses_need_their_records():
    ok = run(
        [
            hyp(
                cause_category="maintenance_side_effect",
                suspected_root_entity="link-core-1-core-2",
                supporting_evidence=["ev-mnt-0001", "ev-topo-0001"],
            )
        ]
    )
    assert "missing_required_context" not in codes(ok)
    bad = run([hyp(cause_category="configuration_change", suspected_root_entity="core-2")])
    assert "missing_required_context" in codes(bad)
    good = run(
        [hyp(cause_category="configuration_change", suspected_root_entity="agg-4", supporting_evidence=["ev-evt-0001"])]
    )
    assert "missing_required_context" not in codes(good)


def test_duplicates_are_dropped_without_blocking():
    out = run([hyp(), hyp(hypothesis_id="hyp-02")])
    assert out.result.passed and out.result.accepted_hypothesis_ids == ["hyp-01"]
    assert out.result.rejected_hypothesis_ids == ["hyp-02"] and "duplicate_hypothesis" in codes(out)


def test_empty_output_with_confirmed_anomalies_is_blocking():
    out = run([])
    assert not out.result.passed and "no_hypotheses" in codes(out)
    assert run([], anomalies=[]).result.passed  # nothing observed, nothing to explain


def test_one_bad_hypothesis_does_not_reject_the_good_one():
    out = run([hyp(), hyp(hypothesis_id="hyp-02", suspected_root_entity="core-9", cause_category="unknown")])
    assert out.result.accepted_hypothesis_ids == ["hyp-01"] and out.result.rejected_hypothesis_ids == ["hyp-02"]
    assert not out.result.passed  # still triggers a bounded retry
