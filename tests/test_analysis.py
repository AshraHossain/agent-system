import pytest

from opspilot.core.analysis import compare_with_incident, generate_candidates
from opspilot.core.errors import NotFound
from opspilot.core.telemetry import summarize_anomalies
from opspilot.datasets.spec import get_case


def _cands(ds_factory, topo_factory, windows, case):
    ds = ds_factory(case)
    return generate_candidates(ds, topo_factory(case), *windows(ds))


@pytest.mark.parametrize(
    "case", ["C02", "C03", "C04", "C06", "C07", "C08", "C09", "C10", "C13", "C14"]
)
def test_top1_matches_label(ds_factory, topo_factory, windows, case):
    cs = _cands(ds_factory, topo_factory, windows, case)
    exp = get_case(case)["labels"]["root_causes"][0]
    top = cs.hypotheses[0]
    assert (top.category.value, top.component_id) == (exp["category"], exp["component"])


def test_multiple_faults_both_in_top3(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C05")
    top3 = {(h.category.value, h.component_id) for h in cs.hypotheses[:3]}
    assert {("link_congestion", "lnk-l4-s1"), ("dns_degradation", "dns-1")} <= top3


def test_normal_operations_produce_no_hypotheses(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C01")
    assert cs.hypotheses == [] and cs.affected_services == []


def test_historical_similarity_is_not_support(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C08")
    top = cs.hypotheses[0]
    assert "INC-2025-0412" not in top.historical_references
    assert any("INC-2025-0412" in a and "not anomalous" in a for a in top.alternative_explanations)
    ev = {e.evidence_id: e for e in cs.evidence}
    assert all(ev[i].kind.value != "INC" for i in top.supporting_evidence_ids)


def test_contradiction_produces_competing_hypotheses(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C07")
    cats = [h.category.value for h in cs.hypotheses]
    assert cats[:2] == ["telemetry_artifact", "link_physical_degradation"]
    assert cs.hypotheses[1].confidence == "weak" and cs.hypotheses[1].contradicting_evidence_ids
    assert cs.hypotheses[0].confidence == "moderate"  # capped: needs confirmation


def test_incomplete_data_lowers_confidence(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C06")
    assert cs.hypotheses[0].confidence == "moderate"
    assert cs.additional_evidence_requests


def test_unexplained_service_with_incomplete_topology(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "C14")
    assert any(o.startswith("video-stream") for o in cs.unexplained_observations)


def test_compare_with_incident(ds_factory, windows):
    ds = ds_factory("C08")
    s = summarize_anomalies(ds, *windows(ds))
    cmp = compare_with_incident(ds, "INC-2025-0412", s)
    assert cmp.signature_matches_current is False and cmp.similarities and cmp.differences
    with pytest.raises(NotFound):
        compare_with_incident(ds, "RB-001", s)
