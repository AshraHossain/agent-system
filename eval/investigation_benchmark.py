"""Investigation-layer benchmark: run every case end to end, then score against labels.

This scorer reads ground-truth labels, so it lives in ``eval/``, never in ``netpulse``.
Every investigation finishes before any label file is opened.

Metric definitions follow PLAN.md section 4. Choices that the plan leaves open:

* Root-cause match: ``cause_category`` equals a labeled category and ``suspected_root_entity``
  is one of that label's entities. Cases with no labeled root cause are excluded from top-k.
* Citation validity: a cited ID is valid if it exists in the evidence registry and either the
  item names no entity or shares one with the hypothesis (root entity or affected components).
* Unsupported claim: a hypothesis rejected by the verifier on any attempt, or whose supporting
  evidence is entirely untrusted (or empty).
* Escalation (gated): predicted positive = final outcome ``escalated`` or the report lists escalation reasons
  (a run that pauses for senior approval because of multiple faults or critical severity escalates).
  The plan-literal variant (outcome ``escalated`` or ``inconclusive``) is reported as ``escalation_plan_*``;
  it counts every healthy-network "inconclusive" as an escalation, which the labels do not.
* Tool-call success: node-trace entries with outcome ``ok`` or ``degraded`` over all non-interrupted entries.
* Latency: sum of node durations excluding ``human_approval``; cost: no provider reports tokens here.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.graph.runner import initial_state, resume_investigation, run_investigation

ROOT = Path(__file__).resolve().parent.parent
FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
EVAL_REVIEW = {"reviewer_id": "eval-harness", "reviewer_role": "senior_operator", "choice": "approve"}
OUTCOME_FOR_EXPECTED = {
    "root_cause": {"root_cause_identified"},
    "inconclusive": {"inconclusive", "escalated"},
}


def run_case(case: dict, deps: Deps) -> dict[str, Any]:
    """Run one case; a run paused for approval is approved once by a synthetic senior reviewer."""
    state = initial_state(
        case["submission"], submitted_at=case["submitted_at"], incident_id=case["case_id"], source="eval", deps=deps
    )
    graph = build_graph(deps)
    out = run_investigation(deps, state, graph)
    paused = "__interrupt__" in out
    if paused:
        out = resume_investigation(deps, graph, case["case_id"], EVAL_REVIEW)
    out["_paused_for_approval"] = paused
    return out


def _ranked(state: dict) -> list[dict]:
    by_id = {h["hypothesis_id"]: h for h in state.get("hypotheses") or []}
    ranking = (state.get("confidence_assessment") or {}).get("ranking") or []
    return [by_id[r["hypothesis_id"]] for r in sorted(ranking, key=lambda r: r["rank"]) if r["hypothesis_id"] in by_id]


def _matches(hyp: dict, causes: list[dict]) -> bool:
    return any(
        hyp["cause_category"] == c["category"] and hyp.get("suspected_root_entity") in c["entities"] for c in causes
    )


def _citations(state: dict) -> tuple[int, int, list[str]]:
    registry = state.get("evidence_references") or {}
    total = valid = 0
    bad: list[str] = []
    for hyp in state.get("hypotheses") or []:
        concerned = set(hyp.get("affected_components") or []) | {hyp.get("suspected_root_entity")}
        for eid in [*hyp["supporting_evidence"], *hyp["contradicting_evidence"]]:
            total += 1
            item = registry.get(eid)
            entities = set(item.get("entity_ids") or []) if item else set()
            if item is not None and (not entities or entities & concerned):
                valid += 1
            else:
                bad.append(f"{hyp['hypothesis_id']}->{eid}")
    return total, valid, bad


def _unsupported(state: dict) -> tuple[int, int]:
    registry = state.get("evidence_references") or {}
    rejected = {i for v in state.get("verification_results") or [] for i in v.get("rejected_hypothesis_ids", [])}
    hyps = state.get("hypotheses") or []
    bad = 0
    for hyp in hyps:
        trusted = [e for e in hyp["supporting_evidence"] if registry.get(e, {}).get("trusted", False)]
        if hyp["hypothesis_id"] in rejected or not trusted:
            bad += 1
    return len(hyps), bad


def score_case(case: dict, label: dict, state: dict) -> dict[str, Any]:
    report = state.get("final_report") or {}
    outcome = report.get("outcome")
    ranked = _ranked(state)
    causes = label["root_causes"]
    cites_total, cites_valid, bad_cites = _citations(state)
    hyp_total, hyp_unsupported = _unsupported(state)
    recommended = {a["catalog_id"] for a in state.get("recommended_actions") or []}
    acceptable = set(label["acceptable_actions"])
    union = recommended | acceptable
    trace = state.get("node_trace") or []
    counted = [t for t in trace if t["outcome"] != "interrupted"]
    row: dict[str, Any] = {
        "case_id": case["case_id"],
        "scenario": label["scenario"],
        "expected_outcome": label["expected_outcome"],
        "outcome": outcome,
        "outcome_correct": outcome in OUTCOME_FOR_EXPECTED[label["expected_outcome"]],
        "should_escalate": label["should_escalate"],
        "predicted_escalate": outcome == "escalated" or bool(report.get("escalation_reasons")),
        "predicted_escalate_plan": outcome in {"escalated", "inconclusive"},
        "top1": _matches(ranked[0], causes) if causes and ranked else None,
        "top3": any(_matches(h, causes) for h in ranked[:3]) if causes else None,
        "predicted_top": [f"{h['cause_category']}@{h.get('suspected_root_entity')}" for h in ranked[:3]],
        "expected_causes": [f"{c['category']}@{','.join(c['entities'])}" for c in causes],
        "citations_total": cites_total,
        "citations_valid": cites_valid,
        "invalid_citations": bad_cites,
        "hypotheses_total": hyp_total,
        "hypotheses_unsupported": hyp_unsupported,
        "action_jaccard": len(recommended & acceptable) / len(union) if union else 1.0,
        "action_recall": len(recommended & acceptable) / len(acceptable) if acceptable else 1.0,
        "missing_actions": sorted(acceptable - recommended),
        "extra_actions": sorted(recommended - acceptable),
        "tool_calls": len(counted),
        "tool_calls_ok": sum(t["outcome"] in {"ok", "degraded"} for t in counted),
        "errors": len(state.get("errors") or []),
        "latency_ms": sum(t["duration_ms"] for t in trace if t["node"] != "human_approval"),
        "generated_by": sorted({h["generated_by"] for h in state.get("hypotheses") or []}),
        "paused_for_approval": state.get("_paused_for_approval", False),
        "executed_actions": sum(bool(a.get("executed")) for a in state.get("recommended_actions") or []),
    }
    row["failures"] = failure_analysis(row)
    return row


def failure_analysis(row: dict) -> list[str]:
    notes = []
    if not row["outcome_correct"]:
        notes.append(f"outcome {row['outcome']} but expected {row['expected_outcome']}")
    if row["top1"] is False:
        notes.append(f"top-1 miss: predicted {row['predicted_top'][:1]}, expected {row['expected_causes']}")
    if row["top1"] and row["expected_causes"] and len(row["expected_causes"]) > 1 and not row["top3"]:
        notes.append("not all causes recovered")
    if row["predicted_escalate"] != row["should_escalate"]:
        kind = "unneeded escalation" if row["predicted_escalate"] else "missed escalation"
        notes.append(f"{kind} (outcome {row['outcome']})")
    if row["invalid_citations"]:
        notes.append(f"invalid citations: {row['invalid_citations'][:5]}")
    if row["missing_actions"]:
        notes.append(f"acceptable actions not recommended: {row['missing_actions']}")
    if row["errors"]:
        notes.append(f"{row['errors']} recorded errors")
    return notes


def _ratio(num: int, den: int) -> float:
    return num / den if den else 1.0


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 1.0


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def aggregate(rows: list[dict]) -> dict[str, Any]:
    with_cause = [r for r in rows if r["top1"] is not None]
    tp = sum(r["predicted_escalate"] and r["should_escalate"] for r in rows)
    fp = sum(r["predicted_escalate"] and not r["should_escalate"] for r in rows)
    fn = sum(not r["predicted_escalate"] and r["should_escalate"] for r in rows)
    ptp = sum(r["predicted_escalate_plan"] and r["should_escalate"] for r in rows)
    pfp = sum(r["predicted_escalate_plan"] and not r["should_escalate"] for r in rows)
    pfn = sum(not r["predicted_escalate_plan"] and r["should_escalate"] for r in rows)
    root_rows = [r for r in rows if r["expected_outcome"] == "root_cause"]
    latencies = [r["latency_ms"] for r in rows]
    return {
        "cases": len(rows),
        "outcome_accuracy": _ratio(sum(r["outcome_correct"] for r in rows), len(rows)),
        "root_cause_top1": _ratio(sum(bool(r["top1"]) for r in with_cause), len(with_cause)),
        "root_cause_top3": _ratio(sum(bool(r["top3"]) for r in with_cause), len(with_cause)),
        "citation_validity": _ratio(sum(r["citations_valid"] for r in rows), sum(r["citations_total"] for r in rows)),
        "unsupported_claim_rate": _ratio(
            sum(r["hypotheses_unsupported"] for r in rows), sum(r["hypotheses_total"] for r in rows)
        )
        if sum(r["hypotheses_total"] for r in rows)
        else 0.0,
        "recommendation_jaccard": _mean([r["action_jaccard"] for r in root_rows]),
        "recommendation_recall": _mean([r["action_recall"] for r in root_rows]),
        "escalation_precision": _ratio(tp, tp + fp),
        "escalation_recall": _ratio(tp, tp + fn),
        "escalation_plan_precision": _ratio(ptp, ptp + pfp),
        "escalation_plan_recall": _ratio(ptp, ptp + pfn),
        "tool_call_success_rate": _ratio(sum(r["tool_calls_ok"] for r in rows), sum(r["tool_calls"] for r in rows)),
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "cost_usd": 0.0,
        "executed_actions": sum(r["executed_actions"] for r in rows),
    }


def run(
    provider: str = "heuristic",
    cases_file: Path = ROOT / "eval/datasets/v1/cases.jsonl",
    labels_file: Path = ROOT / "eval/labels/v1/labels.jsonl",
) -> dict[str, Any]:
    cases = [json.loads(line) for line in cases_file.read_text().splitlines() if line.strip()]
    deps = Deps.default(provider=provider)
    deps.clock = lambda: FIXED_NOW
    states = {c["case_id"]: run_case(c, deps) for c in cases}  # all runs finish before labels are read
    labels = {lab["case_id"]: lab for lab in map(json.loads, labels_file.read_text().splitlines())}
    rows = [score_case(c, labels[c["case_id"]], states[c["case_id"]]) for c in cases]
    generators = sorted({g for r in rows for g in r["generated_by"]})
    return {"provider": provider, "generators_used": generators, "metrics": aggregate(rows), "cases": rows}
