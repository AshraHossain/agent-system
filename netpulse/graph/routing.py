"""Pure routing functions: state (+ current time) in, next node name out.

Order of checks for every router: fatal error → failure_report; deadline passed
→ escalate; then the router's own rule. Each is unit-tested with hand-built
state dicts (tests/test_routing.py).
"""

from __future__ import annotations

from datetime import datetime

from netpulse.models import Budget, ConfidenceAssessment

FAILURE, ESCALATE = "failure_report", "escalate"


def budget(state: dict) -> Budget:
    return Budget.model_validate(state.get("budget") or {})


def deadline_passed(state: dict, now: datetime) -> bool:
    deadline = state.get("deadline_at")
    return bool(deadline) and now > datetime.fromisoformat(deadline)


def guard(state: dict, now: datetime) -> str | None:
    if state.get("fatal_error"):
        return FAILURE
    if deadline_passed(state, now):
        return ESCALATE
    return None


def after_linear(next_node: str):
    def route(state: dict, now: datetime) -> str:
        return guard(state, now) or next_node

    route.__name__ = f"route_to_{next_node}"
    return route


def after_verify(state: dict, now: datetime) -> str:
    """Retry generation while blocking issues remain and the retry budget allows; else rank what passed."""
    if stop := guard(state, now):
        return stop
    latest = (state.get("verification_results") or [{}])[-1]
    if latest.get("passed", True):
        return "rank_hypotheses"
    if state.get("retry_count", 0) <= budget(state).max_hypothesis_retries:
        return "generate_hypotheses"
    return "rank_hypotheses"


def after_rank(state: dict, now: datetime) -> str:
    """Sufficient → recommend; insufficient → another (wider) investigation round while rounds remain."""
    if stop := guard(state, now):
        return stop
    assessment = ConfidenceAssessment.model_validate(state["confidence_assessment"])
    if assessment.evidence_sufficient:
        return "recommend_actions"
    has_signal = any(a.get("confirmed") for a in state.get("detected_anomalies") or [])
    if has_signal and state.get("investigation_rounds", 1) < budget(state).max_investigation_rounds:
        return "retrieve_telemetry"
    return "recommend_actions"


def after_recommend(state: dict, now: datetime) -> str:
    return guard(state, now) or "policy_review"


def after_policy(state: dict, now: datetime) -> str:
    if stop := guard(state, now):
        return stop
    return {"pending": "human_approval", "escalated": ESCALATE}.get(state.get("approval_status"), "compile_report")


def after_human(state: dict, now: datetime) -> str:
    """After a reviewer resumes the run. No deadline check: the deadline is re-based on resume."""
    if state.get("fatal_error"):
        return FAILURE
    if state.get("review_error"):
        return "human_approval"  # invalid input: ask again (each loop needs a new human resume)
    status = state.get("approval_status")
    if status == "more_investigation_requested":
        return "retrieve_telemetry" if _review_cycles_left(state) >= 0 else ESCALATE
    return "compile_report"


def _review_cycles_left(state: dict) -> int:
    used = sum(1 for d in state.get("reviewer_decisions") or [] if d["choice"] == "request_more_investigation")
    return budget(state).max_review_cycles - used


def after_escalate(state: dict, now: datetime) -> str:
    return FAILURE if state.get("fatal_error") else "compile_report"
