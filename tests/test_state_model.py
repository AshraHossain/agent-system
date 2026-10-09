"""Contract tests for the NetPulse typed state model (Phase 2)."""

from datetime import UTC, datetime, timedelta

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from netpulse.models import (
    SYNTHETIC_DATA_NOTICE,
    ApprovalStatus,
    Budget,
    EvidenceItem,
    EvidenceSource,
    FinalReport,
    Hypothesis,
    IncidentSubmission,
    ProposedAction,
    ReportOutcome,
)
from netpulse.state import EvidenceConflictError, InvestigationState, append_list, merge_evidence

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(evidence_id: str = "ev-anom-0001", summary: str = "loss 4.2% on link-a") -> dict:
    return EvidenceItem(evidence_id=evidence_id, source=EvidenceSource.DETECTOR, summary=summary).model_dump(
        mode="json"
    )


def test_submission_rejects_inverted_window():
    with pytest.raises(ValidationError):
        IncidentSubmission(title="loss", dataset_id="ds1", window_start=T0, window_end=T0 - timedelta(minutes=1))


def test_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        IncidentSubmission(
            title="loss", dataset_id="ds1", window_start=T0, window_end=T0 + timedelta(hours=1), root_cause="x"
        )


def test_evidence_is_synthetic_by_construction():
    item = EvidenceItem(evidence_id="ev-tel-0001", source=EvidenceSource.TELEMETRY, summary="cpu 97%")
    assert item.provenance == "synthetic"
    with pytest.raises(ValidationError):
        EvidenceItem(evidence_id="ev-tel-0001", source="telemetry", summary="x", provenance="production")


def test_evidence_id_format_enforced():
    with pytest.raises(ValidationError):
        EvidenceItem(evidence_id="made-up", source=EvidenceSource.TELEMETRY, summary="x")


def test_hypothesis_has_no_numeric_confidence_field():
    fields = Hypothesis.model_fields
    assert "confidence" not in fields and "probability" not in fields
    with pytest.raises(ValidationError):
        Hypothesis(
            hypothesis_id="hyp-01",
            description="Fibre degradation on core link",
            cause_category="link_degradation",
            confidence_rationale="cited",
            generated_by="llm",
            confidence=0.93,
        )


def test_proposed_action_cannot_be_marked_executed():
    with pytest.raises(ValidationError):
        ProposedAction(
            action_id="act-01",
            catalog_id="restart_interface",
            kind="remediation",
            description="x",
            reversible=True,
            executed=True,
        )


def test_budget_bounds_are_enforced():
    with pytest.raises(ValidationError):
        Budget(max_hypothesis_retries=50)


def test_final_report_carries_synthetic_notice():
    report = FinalReport(
        incident_id="inc-1",
        outcome=ReportOutcome.INCONCLUSIVE,
        summary="insufficient evidence",
        approval_status=ApprovalStatus.ESCALATED,
        generated_at=T0,
    )
    assert report.data_notice == SYNTHETIC_DATA_NOTICE


def test_merge_evidence_adds_and_is_idempotent():
    merged = merge_evidence({}, {"ev-anom-0001": _evidence()})
    assert merge_evidence(merged, {"ev-anom-0001": _evidence()}) == merged


def test_merge_evidence_rejects_mutation():
    merged = merge_evidence({}, {"ev-anom-0001": _evidence()})
    with pytest.raises(EvidenceConflictError):
        merge_evidence(merged, {"ev-anom-0001": _evidence(summary="loss 0.1%")})


def test_append_list_preserves_order():
    assert append_list([1], [2, 3]) == [1, 2, 3]
    assert append_list(None, None) == []


def test_reducers_apply_inside_langgraph():
    def first(_: InvestigationState):
        return {"evidence_references": {"ev-anom-0001": _evidence()}, "errors": [{"n": 1}], "status": "running"}

    def second(_: InvestigationState):
        return {
            "evidence_references": {"ev-topo-0001": _evidence("ev-topo-0001")},
            "errors": [{"n": 2}],
            "status": "completed",
        }

    g = StateGraph(InvestigationState)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    out = g.compile().invoke({"incident_id": "inc-1"})

    assert set(out["evidence_references"]) == {"ev-anom-0001", "ev-topo-0001"}
    assert [e["n"] for e in out["errors"]] == [1, 2]
    assert out["status"] == "completed"  # plain fields are replaced
