"""Builders for hand-made findings used by verification/report unit tests."""

from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.findings import (
    Hypothesis,
    IncidentAnalysis,
    KeyFact,
    KnowledgeFinding,
    RecommendedStep,
    ReportDraft,
    TelemetryFinding,
    TopologyFinding,
)

TEL = Evidence.make(
    EvidenceKind.TELEMETRY,
    "lnk-l1-s1 util critical",
    "telemetry:lnk-l1-s1/u",
    entity_id="lnk-l1-s1",
    metric="utilization_pct",
    data={"verdict": "critical"},
)
TEL2 = Evidence.make(
    EvidenceKind.TELEMETRY,
    "lnk-l1-s1 latency critical",
    "telemetry:lnk-l1-s1/l",
    entity_id="lnk-l1-s1",
    metric="latency_ms",
    data={"verdict": "critical"},
)
CONTRA = Evidence.make(
    EvidenceKind.TELEMETRY,
    "probe normal",
    "telemetry:lnk-l1-s1/p",
    entity_id="lnk-l1-s1",
    metric="probe_loss_pct",
    data={"verdict": "normal"},
)
DOC = Evidence.make(
    EvidenceKind.DOCUMENT,
    "RB-001 runbook",
    "document:RB-001",
    entity_id="RB-001",
    data={"status": "current"},
    trusted=False,
)
OLD = Evidence.make(
    EvidenceKind.DOCUMENT,
    "RB-003 runbook",
    "document:RB-003",
    entity_id="RB-003",
    data={"status": "deprecated"},
    trusted=False,
)
EVIDENCE = {e.evidence_id: e for e in (TEL, TEL2, CONTRA, DOC, OLD)}


def hyp(**kw):
    base = dict(
        hypothesis_id="H1",
        category="link_congestion",
        component_id="lnk-l1-s1",
        statement="Congestion on lnk-l1-s1",
        supporting_evidence_ids=[TEL.evidence_id, TEL2.evidence_id],
        contradicting_evidence_ids=[],
        historical_references=[],
        explained_services=["checkout"],
        confidence="strong",
        confidence_rationale="strong",
        alternative_explanations=[],
        diagnostic_checks=["Show utilization on lnk-l1-s1"],
    )
    base.update(kw)
    return Hypothesis(**base)


def findings(**overrides):
    tel = TelemetryFinding(
        status="completed",
        summary="s",
        time_window="w",
        anomalies=[],
        affected_entities=["lnk-l1-s1"],
        affected_services=["checkout"],
        data_gaps=[],
        evidence_ids=[TEL.evidence_id],
    )
    top = TopologyFinding(
        status="completed",
        summary="s",
        services_examined=["checkout"],
        shared_dependencies=[],
        blast_radius=[],
        propagation_paths=[],
        uncertainties=[],
        evidence_ids=[],
    )
    kn = KnowledgeFinding(
        status="completed",
        summary="s",
        relevant_runbooks=[],
        related_incidents=[],
        technical_references=[],
        outdated_or_conflicting=[],
        suspicious_documents=[],
        evidence_ids=[DOC.evidence_id],
    )
    an = IncidentAnalysis(status="completed", hypotheses=[hyp()], unexplained_observations=[])
    dr = ReportDraft(
        summary="Congestion on lnk-l1-s1.",
        key_facts=[KeyFact(statement="lnk-l1-s1 utilization high", evidence_ids=[TEL.evidence_id])],
        affected_services=["checkout"],
        affected_components=["lnk-l1-s1"],
        recommended_steps=[
            RecommendedStep(
                step="Show utilization on lnk-l1-s1",
                rationale="r",
                runbook_ids=["RB-001"],
                evidence_ids=[TEL.evidence_id],
            )
        ],
        risk_and_impact="high",
        open_questions=[],
    )
    out = dict(telemetry=tel, topology=top, knowledge=kn, analysis=an, draft=dr)
    out.update(overrides)
    return out
