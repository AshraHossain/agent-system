"""Local-model integration profile. Skipped by default.

    ollama pull qwen2.5:7b-instruct
    NETPULSE_OLLAMA_TESTS=1 uv run pytest -m ollama

These tests assert workflow *invariants* that must hold whatever the model
says: citations resolve, verification ran, insufficient evidence is never
reported as a confident cause, and remediation is never executed. They do
not assert the model is right; that is measured by the evaluation harness.
"""

import os

import pytest
from graph_helpers import case_state

from netpulse.config import Settings
from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.graph.runner import run_investigation

pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(os.environ.get("NETPULSE_OLLAMA_TESTS") != "1", reason="set NETPULSE_OLLAMA_TESTS=1"),
]


@pytest.fixture(scope="module")
def deps() -> Deps:
    settings = Settings.from_env().model_copy(update={"llm_fallback": False, "llm_provider": "ollama"})
    return Deps.default(settings=settings)


@pytest.mark.parametrize("scenario", ["congestion", "outside_evidence", "prompt_injection", "missing_data"])
def test_invariants_hold_with_real_model(deps, scenario):
    state = run_investigation(deps, case_state(scenario, deps), build_graph(deps))
    report = state["final_report"]
    assert report["outcome"] != "failed", state["errors"]
    assert state["verification_results"], "verification must run"
    assert all(a["provider"] == "ollama" for a in state["generation_attempts"])
    accepted = set(state["verification_results"][-1]["accepted_hypothesis_ids"])
    for h in state["hypotheses"]:
        if h["hypothesis_id"] in accepted:
            assert set(h["supporting_evidence"]) <= set(state["evidence_references"])
    assert all(a["kind"] == "diagnostic" and not a["executed"] for a in report["recommended_actions"])
    if scenario in {"outside_evidence", "missing_data"}:
        assert report["outcome"] != "root_cause_identified"
