"""Shared helpers for graph tests: case loading and manual node execution with real reducers."""

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from netpulse.graph.builder import PIPELINE
from netpulse.graph.deps import Deps
from netpulse.graph.runner import initial_state
from netpulse.state import append_list, merge_evidence

ROOT = Path(__file__).resolve().parent.parent
FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

CASES = {c["case_id"]: c for c in map(json.loads, (ROOT / "eval/datasets/v1/cases.jsonl").read_text().splitlines())}
# Tests may consult labels to choose cases; netpulse never does.
SCENARIO_CASE = {
    lab["scenario"]: lab["case_id"]
    for lab in map(json.loads, (ROOT / "eval/labels/v1/labels.jsonl").read_text().splitlines())
}


@cache
def shared_deps() -> Deps:
    deps = Deps.default()
    deps.clock = lambda: FIXED_NOW
    return deps


def case_state(scenario: str, deps: Deps | None = None) -> dict:
    case = CASES[SCENARIO_CASE[scenario]]
    return initial_state(
        case["submission"],
        submitted_at=case["submitted_at"],
        incident_id=case["case_id"],
        source="test",
        deps=deps or shared_deps(),
    )


def apply(state: dict, update: dict) -> dict:
    """Apply a node update the way LangGraph would, using the real reducers."""
    out = dict(state)
    for key, value in update.items():
        if key.startswith("_"):
            continue
        if key == "evidence_references":
            out[key] = merge_evidence(out.get(key), value)
        elif key in {"errors", "node_trace", "verification_results", "reviewer_decisions"}:
            out[key] = append_list(out.get(key), value)
        else:
            out[key] = value
    return out


def run_until(scenario: str, stop_before: str, deps: Deps | None = None) -> dict:
    """Run node functions (unwrapped) in pipeline order up to, not including, ``stop_before``."""
    deps = deps or shared_deps()
    state = case_state(scenario, deps)
    for name, fn in PIPELINE:
        if name == stop_before:
            return state
        state = apply(state, fn(state, deps))
    return state
