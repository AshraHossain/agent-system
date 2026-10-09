"""Phase 6: unit tests for individual graph nodes (deterministic, no LLM)."""

import pytest
from graph_helpers import FIXED_NOW, apply, case_state, run_until, shared_deps

from netpulse.errors import ToolInputError
from netpulse.graph import nodes
from netpulse.graph.wrapper import instrument
from netpulse.llm.base import GenerationResult
from netpulse.models import EvidenceItem, Hypothesis

# --- intake ----------------------------------------------------------------------


def test_intake_initialises_control_fields():
    out = nodes.intake_validate(case_state("congestion"), shared_deps())
    assert out["retry_count"] == 0 and out["investigation_rounds"] == 0 and out["status"] == "running"
    assert out["approval_status"] == "not_required" and out["fatal_error"] is False
    assert out["deadline_at"] > FIXED_NOW.isoformat()


def test_intake_rejects_invalid_submission():
    state = case_state("congestion")
    state["submission"] = {**state["submission"], "title": ""}
    with pytest.raises(ToolInputError):
        nodes.intake_validate(state, shared_deps())


def test_intake_rejects_window_after_submission_time():
    state = case_state("congestion")
    state["request_metadata"] = {**state["request_metadata"], "submitted_at": state["submission"]["window_start"]}
    with pytest.raises(ToolInputError, match="future"):
        nodes.intake_validate(state, shared_deps())


def test_intake_drops_unknown_suspects_and_flags_injection():
    state = case_state("prompt_injection")
    state["submission"] = {**state["submission"], "suspected_entities": ["core-1", "router-x"]}
    out = nodes.intake_validate(state, shared_deps())
    assert out["submission"]["suspected_entities"] == ["core-1"]
    assert any("router-x" in w for w in out["input_warnings"])
    assert any("instruction-like" in w for w in out["input_warnings"])


def test_wrapped_intake_failure_is_fatal_and_recorded():
    state = case_state("congestion")
    state["submission"] = {}
    out = instrument("intake_validate", nodes.intake_validate, shared_deps(), critical=True)(state)
    assert out["fatal_error"] is True
    assert out["errors"][0]["kind"] == "validation" and out["node_trace"][0]["outcome"] == "error"


# --- classification ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("CPU high on router", "device_resource"),
        ("Utilization alarm on uplink", "congestion"),
        ("Calls are choppy", "packet_loss"),
        ("Pages slow to load", "latency"),
        ("Something feels odd", "unknown"),
    ],
)
def test_classification_rules(title, expected):
    state = case_state("congestion")
    state["submission"] = {**state["submission"], "title": title, "description": "", "severity_hint": None}
    out = nodes.classify_incident(state, shared_deps())["classification"]
    assert out["category"] == expected and out["severity"] == "medium"
    assert "not evidence of a cause" in out["rationale"]


# --- telemetry and detection ---------------------------------------------------------


def test_retrieve_telemetry_records_gaps_as_evidence():
    state = run_until("missing_data", "retrieve_telemetry")
    out = nodes.retrieve_telemetry(state, shared_deps())
    assert out["investigation_rounds"] == 1
    assert "link-agg-3-acc-6" in out["telemetry_window"]["gap_entities"]
    dq = [EvidenceItem.model_validate(e) for e in out["evidence_references"].values()]
    assert dq and all(e.source == "data_quality" for e in dq)
    assert out["telemetry_window"]["data_quality_evidence_ids"] == sorted(out["evidence_references"])


def test_detect_anomalies_links_every_anomaly_to_evidence():
    state = run_until("congestion", "detect_anomalies")
    out = nodes.detect_anomalies(state, shared_deps())
    assert out["detected_anomalies"]
    for a in out["detected_anomalies"]:
        item = EvidenceItem.model_validate(out["evidence_references"][a["evidence_id"]])
        assert item.source == "detector" and item.observed_value == a["peak_value"]
        assert a["entity_id"] in item.entity_ids


def test_detector_failure_is_recorded_not_fatal(monkeypatch):
    from netpulse.detection import engine

    def boom(*args, **kwargs):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(engine.alg, "robust_zscore", boom)
    state = run_until("congestion", "detect_anomalies")
    out = nodes.detect_anomalies(state, shared_deps())
    assert out["errors"] and all(e["recoverable"] and e["kind"] == "tool_failure" for e in out["errors"])
    assert any(a["confirmed"] for a in out["detected_anomalies"])  # other methods still corroborate


# --- topology ----------------------------------------------------------------------


def test_topology_without_anomalies_is_empty():
    state = run_until("normal_quiet", "analyze_topology")
    out = nodes.analyze_topology(state, shared_deps())
    assert out["affected_nodes"] == [] and out["topology_evidence"]["localization"] is None


def test_topology_localizes_shared_link_and_records_context():
    state = run_until("correlated_upstream_link", "analyze_topology")
    out = nodes.analyze_topology(state, shared_deps())
    assert out["topology_evidence"]["localization"]["minimal_cover"] == ["link-fw-1-core-1"]
    assert {"voip", "enterprise_vpn"} <= set(out["affected_services"])
    assert all(e.startswith("ev-topo") for e in out["topology_evidence"]["evidence_ids"])


def test_topology_attaches_change_events_and_maintenance():
    acl = nodes.analyze_topology(run_until("acl_change_regression", "analyze_topology"), shared_deps())
    assert any(k.startswith("ev-evt") for k in acl["evidence_references"])
    maint = nodes.analyze_topology(run_until("maintenance_side_effect", "analyze_topology"), shared_deps())
    assert any(k.startswith("ev-mnt") for k in maint["evidence_references"])


# --- retrieval ----------------------------------------------------------------------


def test_retrieval_evidence_is_untrusted_and_conflicts_are_recorded():
    state = run_until("conflicting_runbooks", "retrieve_runbooks")
    out = nodes.retrieve_runbooks(state, shared_deps())
    items = [EvidenceItem.model_validate(e) for e in out["evidence_references"].values()]
    assert items and not any(i.trusted for i in items)
    assert any(i.method == "declared_conflict" and "RB-002" in i.summary for i in items)


def test_history_respects_submission_time():
    state = run_until("irrelevant_history", "retrieve_history")
    out = nodes.retrieve_history(state, shared_deps())
    assert out["historical_incidents"] and all(r["kind"] == "historical_incident" for r in out["historical_incidents"])


# --- generation, ranking, recommendations, report ------------------------------------------


class ExplodingGenerator:
    provider, model = "fake", None

    def generate(self, context):
        raise ValueError("model returned garbage")


class EchoGenerator:
    """Returns a fixed hypothesis citing whatever evidence ids it is given."""

    provider, model = "fake", "echo"

    def __init__(self, cite: list[str]):
        self.cite = cite

    def generate(self, context):
        hyp = Hypothesis(
            hypothesis_id="hyp-01",
            description="Inferred cause for test purposes",
            cause_category="unknown",
            supporting_evidence=self.cite,
            confidence_rationale="test",
            generated_by="llm",
        )
        return GenerationResult(hypotheses=[hyp], provider=self.provider)


def test_generator_failure_is_recoverable_and_counted():
    deps = shared_deps()
    state = run_until("congestion", "generate_hypotheses")
    original = deps.generator
    deps.generator = ExplodingGenerator()
    try:
        out = nodes.generate_hypotheses(state, deps)
    finally:
        deps.generator = original
    assert out["hypotheses"] == [] and out["retry_count"] == 1
    assert out["errors"][0]["kind"] == "llm_failure" and out["errors"][0]["recoverable"]


def test_generator_receives_only_summaries_and_ids():
    seen = {}

    class Spy:
        provider, model = "spy", None

        def generate(self, context):
            seen["ctx"] = context
            return GenerationResult(hypotheses=[], provider="spy")

    deps = shared_deps()
    state = run_until("congestion", "generate_hypotheses")
    original, deps.generator = deps.generator, Spy()
    try:
        nodes.generate_hypotheses(state, deps)
    finally:
        deps.generator = original
    ctx = seen["ctx"]
    assert {e.evidence_id for e in ctx.evidence} == set(state["evidence_references"])
    assert not hasattr(ctx, "telemetry") and all(len(e.summary) <= 600 for e in ctx.evidence)


def test_recommendations_are_catalog_proposals_never_executed():
    state = run_until("multi_fault", "recommend_actions")
    actions = nodes.recommend_actions(state, shared_deps())["recommended_actions"]
    from netpulse.policy.catalog import load_catalog

    catalog = load_catalog()
    assert actions and all(a["catalog_id"] in catalog and a["executed"] is False for a in actions)
    assert all(catalog[a["catalog_id"]].kind == a["kind"] for a in actions)
    assert any(a["kind"] == "remediation" for a in actions)  # evidence is sufficient here


def test_no_remediation_without_sufficient_evidence():
    state = run_until("outside_evidence", "recommend_actions")
    actions = nodes.recommend_actions(state, shared_deps())["recommended_actions"]
    assert actions and all(a["kind"] == "diagnostic" for a in actions)


def test_recommendations_include_collector_check_when_data_is_missing():
    state = run_until("missing_data", "recommend_actions")
    ids = [a["catalog_id"] for a in nodes.recommend_actions(state, shared_deps())["recommended_actions"]]
    assert "check_collector_health" in ids


def test_report_marks_inconclusive_and_carries_notice():
    state = run_until("outside_evidence", "compile_report")
    out = nodes.compile_report(state, shared_deps())
    report = out["final_report"]
    assert report["outcome"] == "escalated" and out["status"] == "escalated"  # policy escalated it
    assert "SIMULATED DATA" in report["data_notice"] and report["missing_evidence"]


def test_failure_report_survives_malformed_errors():
    state = {"incident_id": "x", "errors": [{"garbage": True}]}
    out = nodes.failure_report(state, shared_deps())
    assert out["final_report"]["outcome"] == "failed" and out["status"] == "failed"


def test_state_update_only_touches_owned_fields():
    state = run_until("congestion", "rank_hypotheses")
    out = nodes.rank_hypotheses(state, shared_deps())
    assert set(out) == {"confidence_assessment"}
    applied = apply(state, out)
    assert applied["hypotheses"] == state["hypotheses"]
