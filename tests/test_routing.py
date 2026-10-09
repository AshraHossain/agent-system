"""Phase 7: routing tables (pure functions over hand-built state)."""

from datetime import UTC, datetime, timedelta

import pytest

from netpulse.graph import routing
from netpulse.models import Budget

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
LATER = (NOW + timedelta(minutes=5)).isoformat()
EARLIER = (NOW - timedelta(seconds=1)).isoformat()


def assessment(sufficient=True, conclusive=True):
    return {
        "overall": "high" if sufficient else "low",
        "evidence_sufficient": sufficient,
        "conclusive": conclusive,
        "ranking": [],
        "missing_evidence": [],
        "rationale": "t",
    }


def state(**kw):
    base = {
        "budget": Budget().model_dump(mode="json"),
        "deadline_at": LATER,
        "retry_count": 1,
        "investigation_rounds": 1,
        "detected_anomalies": [{"confirmed": True}],
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    "router",
    [routing.after_linear("x"), routing.after_verify, routing.after_rank, routing.after_recommend],
)
def test_fatal_beats_deadline_beats_everything(router):
    assert router(state(fatal_error=True, deadline_at=EARLIER), NOW) == "failure_report"
    assert router(state(deadline_at=EARLIER), NOW) == "escalate"


@pytest.mark.parametrize(
    ("passed", "retry_count", "expected"),
    [
        (True, 1, "rank_hypotheses"),
        (False, 1, "generate_hypotheses"),
        (False, 2, "generate_hypotheses"),
        (False, 3, "rank_hypotheses"),  # default max_hypothesis_retries = 2 → at most 3 attempts
        (False, 9, "rank_hypotheses"),
    ],
)
def test_after_verify_retry_budget(passed, retry_count, expected):
    s = state(retry_count=retry_count, verification_results=[{"passed": passed}])
    assert routing.after_verify(s, NOW) == expected


def test_retry_budget_is_configurable_to_zero():
    s = state(budget=Budget(max_hypothesis_retries=0).model_dump(mode="json"), verification_results=[{"passed": False}])
    assert routing.after_verify(s, NOW) == "rank_hypotheses"


@pytest.mark.parametrize(
    ("sufficient", "rounds", "signal", "expected"),
    [
        (True, 1, True, "recommend_actions"),
        (False, 1, True, "retrieve_telemetry"),
        (False, 2, True, "recommend_actions"),  # default max_investigation_rounds = 2
        (False, 1, False, "recommend_actions"),  # nothing observed: a wider window is pointless
    ],
)
def test_after_rank_bounded_rounds(sufficient, rounds, signal, expected):
    s = state(
        confidence_assessment=assessment(sufficient, sufficient),
        investigation_rounds=rounds,
        detected_anomalies=[{"confirmed": signal}],
    )
    assert routing.after_rank(s, NOW) == expected


@pytest.mark.parametrize(
    ("conclusive", "signal", "severity", "expected"),
    [
        (True, True, "high", "compile_report"),
        (False, True, "medium", "escalate"),
        (False, False, "low", "compile_report"),  # nothing found, low severity: inconclusive report only
        (True, True, "critical", "escalate"),
    ],
)
def test_after_recommend_escalation(conclusive, signal, severity, expected):
    s = state(
        confidence_assessment=assessment(conclusive, conclusive),
        detected_anomalies=[{"confirmed": signal}],
        classification={"severity": severity},
    )
    assert routing.after_recommend(s, NOW) == expected


def test_escalate_always_reports_unless_fatal():
    assert routing.after_escalate(state(deadline_at=EARLIER), NOW) == "compile_report"
    assert routing.after_escalate(state(fatal_error=True), NOW) == "failure_report"
