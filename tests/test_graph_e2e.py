"""Phase 6: end-to-end workflow tests through the compiled LangGraph (heuristic generator, no LLM)."""

import builtins
from pathlib import Path

import pytest
from graph_helpers import case_state, shared_deps

from netpulse.graph.builder import build_graph
from netpulse.graph.runner import run_investigation


@pytest.fixture(scope="module")
def graph():
    return build_graph(shared_deps())


def run(scenario: str, graph, deps=None) -> dict:
    deps = deps or shared_deps()
    return run_investigation(deps, case_state(scenario, deps), graph)


def hypotheses_by_rank(state: dict) -> list[dict]:
    hyps = {h["hypothesis_id"]: h for h in state["hypotheses"]}
    return [hyps[r["hypothesis_id"]] for r in state["final_report"]["top_hypotheses"]]


def test_happy_path_identifies_congested_link(graph):
    state = run("congestion", graph)
    report = state["final_report"]
    assert report["outcome"] == "root_cause_identified" and state["status"] == "completed"
    top = hypotheses_by_rank(state)[0]
    assert (top["cause_category"], top["suspected_root_entity"]) == ("link_congestion", "link-core-1-agg-1")
    assert [t["node"] for t in state["node_trace"]] == [
        "intake_validate",
        "classify_incident",
        "retrieve_telemetry",
        "detect_anomalies",
        "analyze_topology",
        "retrieve_history",
        "retrieve_runbooks",
        "generate_hypotheses",
        "verify_evidence",
        "rank_hypotheses",
        "recommend_actions",
        "compile_report",
    ]
    assert report["data_notice"].startswith("SIMULATED DATA")


def test_every_citation_resolves_to_registered_evidence(graph):
    for scenario in ("congestion", "multi_fault", "maintenance_side_effect", "outside_evidence"):
        state = run(scenario, graph)
        registry = state["evidence_references"]
        for h in state["hypotheses"]:
            assert set(h["supporting_evidence"]) <= set(registry), (scenario, h["hypothesis_id"])
        assert set(state["final_report"]["cited_evidence_ids"]) <= set(registry)


@pytest.mark.parametrize("scenario", ["normal_quiet", "normal_noisy"])
def test_no_anomaly_yields_inconclusive_without_invented_cause(graph, scenario):
    state = run(scenario, graph)
    assert state["final_report"]["outcome"] == "inconclusive"
    assert state["hypotheses"] == [] and state["final_report"]["top_hypotheses"] == []


@pytest.mark.parametrize("scenario", ["outside_evidence", "missing_data", "ambiguous"])
def test_insufficient_evidence_never_produces_a_confident_root_cause(graph, scenario):
    state = run(scenario, graph)
    report = state["final_report"]
    assert report["outcome"] == "escalated" and state["approval_status"] == "escalated"
    assert state["escalation_reasons"] and "Escalated to a human operator" in report["summary"]
    assert all(r["confidence"] in ("low", "insufficient_evidence") for r in report["top_hypotheses"])
    assert report["missing_evidence"]


def test_multiple_faults_are_both_reported(graph):
    causes = {(h["cause_category"], h["suspected_root_entity"]) for h in hypotheses_by_rank(run("multi_fault", graph))}
    assert {("device_cpu_saturation", "fw-1"), ("link_degradation", "link-agg-1-acc-2")} <= causes


def test_prompt_injection_is_treated_as_data(graph):
    state = run("prompt_injection", graph)
    report = state["final_report"]
    top = hypotheses_by_rank(state)[0]
    assert (top["cause_category"], top["suspected_root_entity"]) == ("device_cpu_saturation", "core-1")
    assert any("instruction-like" in w for w in state["input_warnings"])
    assert all(a["catalog_id"] != "restart_device" for a in report["recommended_actions"])
    assert all(a["kind"] == "diagnostic" for a in report["recommended_actions"])


def test_missing_dataset_produces_structured_failure_report(graph):
    deps = shared_deps()
    state = case_state("congestion", deps)
    state["submission"] = {**state["submission"], "dataset_id": "case-99"}
    out = run_investigation(deps, state, graph)
    assert out["status"] == "failed" and out["final_report"]["outcome"] == "failed"
    assert out["node_trace"][-1]["node"] == "failure_report"
    assert any(e["node"] == "retrieve_telemetry" and not e["recoverable"] for e in out["errors"])


def test_unexpected_exception_is_contained(graph, monkeypatch):
    deps = shared_deps()

    def broken(*args, **kwargs):
        raise RuntimeError("event store offline")

    monkeypatch.setattr(deps.store, "events", broken)
    out = run_investigation(deps, case_state("congestion", deps), graph)
    assert out["final_report"]["outcome"] == "failed"
    assert any(e["kind"] == "internal" and "event store offline" in e["message"] for e in out["errors"])


def test_runs_are_deterministic_with_fixed_clock(graph):
    a, b = run("acl_change_regression", graph), run("acl_change_regression", graph)
    for key in ("final_report", "hypotheses", "evidence_references", "detected_anomalies"):
        assert a[key] == b[key]


def test_investigation_never_opens_ground_truth(graph, monkeypatch):
    real_open = builtins.open
    real_read_text = Path.read_text
    opened = []

    def guard(path, *args, **kwargs):
        if "labels" in str(path):
            opened.append(str(path))
            raise AssertionError(f"investigation opened ground truth: {path}")
        return real_open(path, *args, **kwargs)

    def guarded_read_text(self, *args, **kwargs):
        return guard(self) and real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard)
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (guard(self).close(), real_read_text(self, *a, **k))[1]
    )
    state = run("congestion", graph)
    assert state["final_report"]["outcome"] == "root_cause_identified" and opened == []
