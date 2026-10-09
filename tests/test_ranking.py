"""Phase 6: deterministic, ordinal confidence ranking."""

from datetime import UTC, datetime

from netpulse.graph.ranking import assess
from netpulse.models import Anomaly, EvidenceItem, Hypothesis

T = datetime(2026, 1, 1, tzinfo=UTC)


def ev(eid: str, source: str = "detector", trusted: bool = True, entities=("r1",)) -> EvidenceItem:
    return EvidenceItem(evidence_id=eid, source=source, summary="x", entity_ids=list(entities), trusted=trusted)


def hyp(hid: str, support: list[str], root: str | None = "r1", category: str = "device_cpu_saturation", **kw):
    return Hypothesis(
        hypothesis_id=hid,
        description="Inferred cause under test",
        cause_category=category,
        suspected_root_entity=root,
        supporting_evidence=support,
        confidence_rationale="t",
        generated_by="llm",
        **kw,
    )


def anomaly(entity: str = "r1", confirmed: bool = True) -> Anomaly:
    return Anomaly(
        anomaly_id="a1",
        entity_id=entity,
        metric="cpu_pct",
        detectors=["robust_zscore"],
        confirmed=confirmed,
        direction="high",
        start=T,
        end=T,
        peak_value=99,
        peak_at=T,
        evidence_id="ev-anom-0001",
    )


REG = {
    e.evidence_id: e
    for e in [
        ev("ev-anom-0001"),
        ev("ev-anom-0002"),
        ev("ev-topo-0001", "topology"),
        ev("ev-rb-0001", "runbook", trusted=False),
        ev("ev-hist-0001", "historical_incident", trusted=False),
        ev("ev-mnt-0001", "maintenance", entities=("l1",)),
    ]
}


def test_untrusted_only_support_is_insufficient():
    a = assess([hyp("hyp-01", ["ev-rb-0001", "ev-hist-0001"])], REG, [anomaly()], [], ["r1"])
    assert a.ranking[0].confidence == "insufficient_evidence" and not a.evidence_sufficient


def test_unknown_evidence_ids_do_not_count():
    a = assess([hyp("hyp-01", ["ev-anom-9999", "ev-topo-9999"])], REG, [anomaly()], [], ["r1"])
    assert a.ranking[0].confidence == "insufficient_evidence" and a.ranking[0].trusted_support_count == 0


def test_unobserved_root_caps_at_low():
    a = assess([hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"], root="r2")], REG, [anomaly()], [], [])
    assert a.ranking[0].confidence == "low" and not a.conclusive


def test_root_with_coverage_gap_caps_at_low():
    a = assess([hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"])], REG, [anomaly()], ["r1"], ["r1"])
    assert a.ranking[0].confidence == "low"


def test_contradicting_evidence_caps_at_low():
    h = hyp("hyp-01", ["ev-anom-0001", "ev-topo-0001"], contradicting_evidence=["ev-anom-0002"])
    assert assess([h], REG, [anomaly()], [], ["r1"]).ranking[0].confidence == "low"


def test_high_and_medium_thresholds():
    high = assess([hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"])], REG, [anomaly()], [], ["r1"])
    medium = assess([hyp("hyp-01", ["ev-anom-0001", "ev-topo-0001"])], REG, [anomaly()], [], ["r1"])
    single_source = assess([hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002"])], REG, [anomaly()], [], ["r1"])
    assert high.ranking[0].confidence == "high" and high.conclusive
    assert medium.ranking[0].confidence == "medium" and medium.evidence_sufficient
    assert single_source.ranking[0].confidence == "low"


def test_unconfirmed_anomaly_does_not_count_as_observation():
    a = assess(
        [hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"])], REG, [anomaly(confirmed=False)], [], []
    )
    assert a.ranking[0].confidence == "low"


def test_maintenance_evidence_observes_its_entity():
    h = hyp("hyp-01", ["ev-mnt-0001", "ev-topo-0001", "ev-anom-0001"], root="l1", category="maintenance_side_effect")
    assert assess([h], REG, [anomaly()], [], ["l1"]).ranking[0].confidence == "high"


def test_tied_competing_hypotheses_are_not_conclusive():
    support = ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"]
    hyps = [hyp("hyp-01", support), hyp("hyp-02", support, category="configuration_change")]
    a = assess(hyps, REG, [anomaly()], [], ["r1"])
    assert a.evidence_sufficient and not a.conclusive  # same root, two explanations: cannot pick


def test_complementary_roots_in_cover_are_conclusive():
    reg = {**REG, "ev-anom-0003": ev("ev-anom-0003", entities=("r2",))}
    hyps = [
        hyp("hyp-01", ["ev-anom-0001", "ev-anom-0002", "ev-topo-0001"]),
        hyp("hyp-02", ["ev-anom-0003", "ev-anom-0002", "ev-topo-0001"], root="r2", category="link_degradation"),
    ]
    a = assess(hyps, reg, [anomaly(), anomaly("r2")], [], ["r1", "r2"])
    assert a.conclusive and [r.rank for r in a.ranking] == [1, 2]


def test_no_hypotheses_is_insufficient():
    a = assess([], REG, [], [], [])
    assert a.overall == "insufficient_evidence" and not a.conclusive and a.missing_evidence
