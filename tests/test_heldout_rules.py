"""Rules added after the held-out baseline (docs/eval_results_heldout.md).

These cases are no longer held out once they drove fixes; they remain as
regression tests for the specific gaps they exposed.
"""

import pytest
from helpers import EVIDENCE, TEL, findings, hyp

from opspilot.contracts.findings import IncidentAnalysis, KeyFact
from opspilot.core.analysis import generate_candidates
from opspilot.core.policy import check_step
from opspilot.core.security import detect_injection
from opspilot.core.telemetry import compare_to_baseline, summarize_anomalies
from opspilot.core.verification import verify
from opspilot.datasets.spec import EXTRA_KNOWLEDGE_DIR, case_ids, get_case, parse_markdown_doc


def _cands(ds_factory, topo_factory, windows, case):
    ds = ds_factory(case)
    return generate_candidates(ds, topo_factory(case), *windows(ds))


def test_recovered_episode_is_detected(ds_factory, windows):
    ds = ds_factory("H07")
    r = compare_to_baseline(ds, "lnk-l2-s2", "utilization_pct", *windows(ds))
    assert r.verdict == "recovered"
    a = next(
        a
        for a in summarize_anomalies(ds, *windows(ds)).anomalies
        if a.entity_id == "lnk-l2-s2" and a.metric == "utilization_pct"
    )
    assert a.first_seen < a.last_seen


def test_no_recovered_false_positives_in_normal_operations(ds_factory, windows):
    for case in ("C01", "H10"):
        ds = ds_factory(case)
        assert summarize_anomalies(ds, *windows(ds)).anomalies == []


def test_recovered_hypothesis_is_labelled_transient(ds_factory, topo_factory, windows):
    top = _cands(ds_factory, topo_factory, windows, "H07").hypotheses[0]
    assert (top.category.value, top.component_id) == ("link_congestion", "lnk-l2-s2")
    assert top.statement.startswith("Transient episode, now recovered")
    assert top.confidence == "moderate"


def test_congestion_inferred_when_utilization_missing(ds_factory, topo_factory, windows):
    top = _cands(ds_factory, topo_factory, windows, "H04").hypotheses[0]
    assert (top.category.value, top.component_id) == ("link_congestion", "lnk-l3-s1")
    assert top.confidence == "moderate" and "utilization telemetry is missing" in top.statement


def test_application_fault_localised_to_upstream_service(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "H05")
    assert [(h.category.value, h.component_id) for h in cs.hypotheses] == [
        ("application_side", "payments")
    ]
    assert cs.hypotheses[0].explained_services == ["checkout", "payments"]


def test_subtle_ai_addressed_injection_is_flagged_and_unsafe_step_rejected():
    body = parse_markdown_doc(EXTRA_KNOWLEDGE_DIR / "INC-2026-0001.md")["body"]
    assert {"ai_addressed", "approval_bypass"} <= set(detect_injection(body))
    assert not check_step("Power off spine-1 so traffic moves to spine-2").allowed


def test_suspicious_history_is_never_used_as_reference(ds_factory, topo_factory, windows):
    cs = _cands(ds_factory, topo_factory, windows, "H08")
    assert all("INC-2026-0001" not in h.historical_references for h in cs.hypotheses)


def test_fact_citing_unrelated_evidence_is_rejected():
    f = findings()
    d = f["draft"].model_copy(
        update={
            "key_facts": [KeyFact(statement="fw-1 is saturated", evidence_ids=[TEL.evidence_id])]
        }
    )
    f["draft"] = d
    res = verify(
        evidence=EVIDENCE,
        known_components={"lnk-l1-s1", "fw-1"},
        known_services={"checkout"},
        suspicious_docs=set(),
        **f,
    )
    assert "fact_citation_mismatch" in {i.code for i in res.issues}
    assert TEL.evidence_id in res.irrelevant_citations


def test_hypothesis_citing_other_component_is_rejected():
    f = findings()
    f["analysis"] = IncidentAnalysis(
        status="completed",
        unexplained_observations=[],
        hypotheses=[hyp(component_id="fw-1", explained_services=[])],
    )
    res = verify(
        evidence=EVIDENCE,
        known_components={"lnk-l1-s1", "fw-1"},
        known_services={"checkout"},
        suspicious_docs=set(),
        **f,
    )
    assert "irrelevant_support" in {i.code for i in res.issues}


def test_link_endpoints_count_as_relevant():
    f = findings()
    res = verify(
        evidence=EVIDENCE,
        known_components={"lnk-l1-s1"},
        known_services={"checkout"},
        suspicious_docs=set(),
        endpoints={"lnk-l1-s1": ["leaf-1", "spine-1"]},
        **f,
    )
    assert res.irrelevant_citations == [] and res.citations_relevance_checked > 0


@pytest.mark.parametrize("case_id", case_ids("heldout"))
async def test_heldout_end_to_end(run_case, case_id):
    labels = get_case(case_id)["labels"]
    r = (await run_case(case_id)).report
    assert r.status.value == labels["status"]
    assert r.escalation.level == labels["escalation"]
    if labels["root_causes"]:
        exp = {(c["category"], c["component"]) for c in labels["root_causes"]}
        assert {(h.category.value, h.component_id) for h in r.root_cause_hypotheses[:3]} >= exp
    for t in labels.get("escalation_targets", []):
        assert t in r.escalation.targets
    assert r.verification.irrelevant_citations == []
