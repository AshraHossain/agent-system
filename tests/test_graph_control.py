"""Phase 7: control flow through the compiled graph with a mocked (scripted) LLM.

Covers bounded retries, verification of hallucinated or contradicted output,
prompt-injection obedience, timeouts, tool failures, deadline escalation, the
step budget, and widening investigation rounds.
"""

import time
from datetime import timedelta

import pytest
from graph_helpers import FIXED_NOW, case_state, run_to_end

from netpulse.errors import DataCorruptError
from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.llm.scripted import ScriptedGenerator
from netpulse.models import Budget


@pytest.fixture(scope="module")
def base_deps() -> Deps:
    deps = Deps.default(provider="heuristic")
    deps.clock = lambda: FIXED_NOW
    return deps


def with_generator(base: Deps, generator) -> Deps:
    return Deps(
        store=base.store, retriever=base.retriever, topology=base.topology, generator=generator, clock=base.clock
    )


def run(deps: Deps, scenario: str, budget: Budget | None = None) -> dict:
    state = case_state(scenario, deps)
    if budget:
        state["budget"] = budget.model_dump(mode="json")
    return run_to_end(deps, state, build_graph(deps))


def anomaly_ids(ctx, entity: str) -> list[str]:
    return [a.evidence_id for a in ctx.anomalies if a.confirmed and a.entity_id == entity]


def congestion_answer(ctx):
    return [
        {
            "description": "Inferred congestion on the west core uplink",
            "cause_category": "link_congestion",
            "suspected_root_entity": "link-core-1-agg-1",
            "supporting_evidence": anomaly_ids(ctx, "link-core-1-agg-1")
            + [e.evidence_id for e in ctx.evidence if e.source == "topology"][:1],
            "confidence_rationale": "detector anomalies on the link and topology localization",
        }
    ]


# --- retries ------------------------------------------------------------------------


def test_hallucinated_id_is_rejected_then_corrected_on_retry(base_deps):
    hallucinated = [
        {
            "description": "Inferred congestion on the west core uplink",
            "cause_category": "link_congestion",
            "suspected_root_entity": "link-core-1-agg-1",
            "supporting_evidence": ["ev-anom-0999"],
            "confidence_rationale": "made-up citation",
        }
    ]
    gen = ScriptedGenerator([hallucinated, congestion_answer])
    state = run(with_generator(base_deps, gen), "congestion")
    assert [v["passed"] for v in state["verification_results"]] == [False, True]
    assert "unknown_evidence_ref" in gen.calls[1].verifier_feedback[0]  # feedback reached the retry
    assert state["final_report"]["outcome"] == "root_cause_identified"
    assert [a["attempt"] for a in state["generation_attempts"]] == [1, 2]


def test_persistently_invalid_output_is_bounded_and_never_fabricates(base_deps):
    gen = ScriptedGenerator(["this is not json"])
    state = run(with_generator(base_deps, gen), "congestion")
    budget = Budget()
    max_calls = (budget.max_hypothesis_retries + 1) * budget.max_investigation_rounds
    assert len(gen.calls) == max_calls == len(state["generation_attempts"])
    assert state["final_report"]["outcome"] == "escalated"
    assert state["final_report"]["top_hypotheses"] == []
    assert all(e["recoverable"] for e in state["errors"] if e["kind"] == "llm_failure")


def test_zero_retry_budget_means_single_attempt_per_round(base_deps):
    gen = ScriptedGenerator(["garbage"])
    run(with_generator(base_deps, gen), "congestion", Budget(max_hypothesis_retries=0, max_investigation_rounds=1))
    assert len(gen.calls) == 1


# --- verification beats the model ------------------------------------------------------


def test_llm_explanation_cannot_override_contradicting_telemetry(base_deps):
    def wrong(ctx):  # a CPU story for a case with no CPU anomaly on core-1
        return [
            {
                "description": "Inferred CPU saturation on core-1 causing VoIP issues",
                "cause_category": "device_cpu_saturation",
                "suspected_root_entity": "core-1",
                "supporting_evidence": anomaly_ids(ctx, "link-core-1-agg-1"),
                "confidence_rationale": "the router is clearly overloaded",
            }
        ]

    state = run(with_generator(base_deps, ScriptedGenerator([wrong])), "congestion")
    issues = [i for v in state["verification_results"] for i in v["issues"]]
    assert any(i["code"] == "contradicted_by_telemetry" for i in issues)
    chk = [k for k in state["evidence_references"] if k.startswith("ev-chk")]
    assert chk and state["final_report"]["top_hypotheses"] == []
    assert state["final_report"]["outcome"] == "escalated"


def test_model_obeying_injected_runbook_is_rejected(base_deps):
    def obey(ctx):  # what a compromised model would say after reading RB-012
        return [
            {
                "description": "Planned maintenance; restart core-1 and close the incident",
                "cause_category": "maintenance_side_effect",
                "suspected_root_entity": "core-1",
                "supporting_evidence": [e.evidence_id for e in ctx.evidence if e.source == "runbook"],
                "confidence_rationale": "the runbook says so",
            }
        ]

    state = run(with_generator(base_deps, ScriptedGenerator([obey])), "prompt_injection")
    codes = {i["code"] for v in state["verification_results"] for i in v["issues"]}
    assert {"untrusted_only_support", "missing_required_context"} & codes
    report = state["final_report"]
    assert report["top_hypotheses"] == [] and all(a["kind"] == "diagnostic" for a in report["recommended_actions"])
    assert all(a["catalog_id"] != "restart_device" for a in report["recommended_actions"])


def test_numbers_invented_by_the_model_are_rejected(base_deps):
    def invented(ctx):
        answer = congestion_answer(ctx)
        answer[0]["description"] = "Uplink saturated at 99.7% for 47 minutes"
        return answer

    state = run(with_generator(base_deps, ScriptedGenerator([invented, congestion_answer])), "congestion")
    assert state["verification_results"][0]["issues"][0]["code"] == "numeric_claim_unsupported"
    assert state["final_report"]["outcome"] == "root_cause_identified"


# --- timeouts, tool failures, budgets -------------------------------------------------------


def test_llm_timeout_counts_as_attempt_and_escalates(base_deps):
    def slow(ctx):
        time.sleep(0.5)
        return congestion_answer(ctx)

    budget = Budget(llm_timeout_seconds=0.1, max_hypothesis_retries=1, max_investigation_rounds=1)
    state = run(with_generator(base_deps, ScriptedGenerator([slow])), "congestion", budget)
    assert [a["outcome"] for a in state["generation_attempts"]] == ["timeout", "timeout"]
    assert any(e["kind"] == "timeout" and e["recoverable"] for e in state["errors"])
    assert state["final_report"]["outcome"] == "escalated" and state["status"] != "failed"


def test_retrieval_failure_degrades_instead_of_failing(base_deps, monkeypatch):
    def broken(query):
        raise DataCorruptError("runbook index corrupt")

    monkeypatch.setattr(base_deps.retriever, "search_runbooks", broken)
    state = run(base_deps, "congestion")
    assert state["final_report"]["outcome"] == "root_cause_identified"
    err = next(e for e in state["errors"] if e["node"] == "retrieve_runbooks")
    assert err["kind"] == "tool_failure" and err["recoverable"]


def test_tool_timeout_degrades_non_critical_node(base_deps, monkeypatch):
    real = base_deps.retriever.search_incidents

    def slow(query):
        time.sleep(4)
        return real(query)

    monkeypatch.setattr(base_deps.retriever, "search_incidents", slow)
    # The budget must exceed the real cost of detection (~1 s) but not the stubbed 4 s retrieval.
    state = run(base_deps, "congestion", Budget(tool_timeout_seconds=2.5))
    assert any(e["node"] == "retrieve_history" and e["kind"] == "timeout" for e in state["errors"])
    assert state["final_report"]["outcome"] == "root_cause_identified"


def test_deadline_routes_to_escalation_with_structured_report(base_deps):
    ticks = iter(range(10_000))
    deps = with_generator(base_deps, base_deps.generator)
    deps.clock = lambda: FIXED_NOW + timedelta(minutes=next(ticks))  # one minute passes per clock read
    state = run(deps, "congestion", Budget(wall_clock_seconds=180))
    report = state["final_report"]
    assert report["outcome"] in {"escalated", "root_cause_identified"} and state["approval_status"] == "escalated"
    assert "investigation time budget exhausted" in state["escalation_reasons"]
    assert any(e["kind"] == "budget_exhausted" for e in state["errors"])


def test_graph_step_budget_produces_failure_report(base_deps):
    state = run(base_deps, "congestion", Budget(max_graph_steps=10))
    assert state["final_report"]["outcome"] == "failed" and state["status"] == "failed"
    assert any(e["kind"] == "budget_exhausted" and "step budget" in e["message"] for e in state["errors"])


def test_second_round_widens_the_window(base_deps):
    state = run(base_deps, "outside_evidence")
    sub = state["submission"]
    assert state["investigation_rounds"] == 2
    assert state["telemetry_window"]["start"] < sub["window_start"]
    assert state["final_report"]["outcome"] == "escalated"
