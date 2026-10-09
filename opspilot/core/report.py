"""Deterministic assembly of the final InvestigationReport.

Status and escalation are computed by explicit rules here — never by an LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime

from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.findings import (
    HypothesisCategory,
    IncidentAnalysis,
    KnowledgeFinding,
    ReportDraft,
    ReviewResult,
    TelemetryFinding,
    TopologyFinding,
)
from opspilot.contracts.report import (
    DiagnosticStep,
    DocRef,
    Escalation,
    EvidenceRef,
    InvestigationReport,
    InvestigationStatus,
    RiskAssessment,
    RunMetrics,
    ServiceRisk,
)
from opspilot.contracts.request import InvestigationScope
from opspilot.contracts.verification import VerificationResult
from opspilot.core.policy import check_step
from opspilot.core.security import redact
from opspilot.core.topology import IMPACT_ORDER, Topology

S = InvestigationStatus


def _ref(ev: Evidence) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=ev.evidence_id,
        kind=ev.kind.value,
        summary=ev.summary,
        source=ev.source,
        trusted=ev.trusted,
    )


def _clean(text: str) -> str:
    return redact(text)[0]


def decide_status(
    *,
    aborted: str | None,
    specialists: dict[str, str],
    analysis: IncidentAnalysis | None,
    verification: VerificationResult | None,
    review: ReviewResult | None,
    evidence: dict[str, Evidence],
    anomalies_present: bool,
    topo: Topology | None = None,
) -> tuple[InvestigationStatus, str]:
    core = ("telemetry", "topology", "knowledge")
    if aborted:
        return S.FAILED, f"run aborted: {aborted}"
    if all(specialists.get(k) in (None, "failed") for k in core):
        return S.FAILED, "all specialist stages failed"
    failed = [k for k, v in specialists.items() if v in (None, "failed")]
    if failed:
        return S.INCONCLUSIVE, f"stage(s) failed: {', '.join(failed)}"
    hyps = analysis.hypotheses if analysis else []
    if anomalies_present and not any(h.confidence in ("strong", "moderate") for h in hyps):
        return S.INCONCLUSIVE, "anomalies exist but no hypothesis has direct support"
    if hyps:
        top = hyps[0]
        related = {top.component_id}
        if topo is not None and top.component_id in topo.components:
            related |= set(topo.components[top.component_id].get("endpoints", []))
        gaps = [
            e
            for e in evidence.values()
            if e.kind == EvidenceKind.DATA_QUALITY and e.entity_id in related
        ]
        if gaps:
            return S.INCONCLUSIVE, (
                f"telemetry for the leading hypothesis' component {top.component_id} is incomplete"
            )
    if verification is not None:
        if any(i.severity == "error" for i in verification.issues):
            return S.REQUIRES_HUMAN_REVIEW, "deterministic verification found errors"
        if verification.contradictions:
            return S.REQUIRES_HUMAN_REVIEW, "contradicting evidence on leading hypotheses"
    if hyps and (
        hyps[0].category == HypothesisCategory.TELEMETRY_ARTIFACT or hyps[0].confidence == "weak"
    ):
        return S.REQUIRES_HUMAN_REVIEW, "leading hypothesis needs human confirmation"
    if review is not None and any(c.severity == "warning" for c in review.concerns):
        return S.REQUIRES_HUMAN_REVIEW, "secondary reviewer raised warnings"
    return S.INVESTIGATED, "all checks passed"


def decide_escalation(
    status: InvestigationStatus,
    risks: list[ServiceRisk],
    security: bool,
    anomalies: bool,
    tiers: dict[str, int],
) -> Escalation:
    targets: list = []
    reasons = []
    max_impact = max((IMPACT_ORDER[r.impact] for r in risks), default=0)
    tier1 = any(tiers.get(r.service) == 1 for r in risks)
    if status == S.FAILED:
        level, reasons = "escalate", ["automated investigation failed"]
    elif status == S.REQUIRES_HUMAN_REVIEW:
        level, reasons = "escalate", ["findings require human review"]
    elif status == S.INCONCLUSIVE:
        level = "escalate" if tier1 else "monitor"
        reasons.append(
            "inconclusive with tier-1 services affected"
            if tier1
            else "inconclusive; no tier-1 impact established"
        )
    elif not anomalies:
        level, reasons = "none", ["no anomalies detected"]
    elif max_impact >= IMPACT_ORDER["high"]:
        level, reasons = "escalate", ["potential impact is high or critical"]
    else:
        level, reasons = "monitor", ["impact limited to medium/low"]
    if level == "escalate":
        targets.append("network_oncall")
    if security:
        targets.append("security_team")
        reasons.append("untrusted content attempted prompt injection")
        level = "escalate"
    return Escalation(level=level, targets=targets, rationale="; ".join(reasons))


def build_report(
    *,
    scope: InvestigationScope,
    evidence: dict[str, Evidence],
    telemetry: TelemetryFinding | None,
    topology: TopologyFinding | None,
    knowledge: KnowledgeFinding | None,
    analysis: IncidentAnalysis | None,
    draft: ReportDraft | None,
    verification: VerificationResult | None,
    review: ReviewResult | None,
    topo: Topology | None,
    run_metrics: RunMetrics | None = None,
    aborted: str | None = None,
) -> InvestigationReport:
    specialists = {
        "telemetry": telemetry.status if telemetry else None,
        "topology": topology.status if topology else None,
        "knowledge": knowledge.status if knowledge else None,
        "incident_analysis": analysis.status if analysis else None,
    }
    anomalies = bool(telemetry and telemetry.anomalies) or any(
        e.kind == EvidenceKind.TELEMETRY and e.data.get("verdict") in ("elevated", "critical")
        for e in evidence.values()
    )
    status, why = decide_status(
        aborted=aborted,
        specialists=specialists,
        analysis=analysis,
        verification=verification,
        review=review,
        evidence=evidence,
        anomalies_present=anomalies,
        topo=topo,
    )
    hyps = analysis.hypotheses if analysis else []
    tiers = {s: topo.tier(s) for s in topo.services} if topo else {}

    # Affected services: only those with telemetry or hypothesis support.
    supported = set(telemetry.affected_services) if telemetry else set()
    for h in hyps:
        supported |= set(h.explained_services)
    affected = sorted(supported)
    components = []
    for h in hyps:
        if h.confidence != "weak" and h.category != HypothesisCategory.APPLICATION:
            components.append(h.component_id)
    if telemetry:
        components += [e for e in telemetry.affected_entities if not e.startswith("svc:")]
    components = list(dict.fromkeys(components))

    # Risk: impact per service from the topology analyst's blast radius for
    # the components behind non-weak hypotheses.
    impacts: dict[str, str] = {}
    if topology:
        relevant = {h.component_id for h in hyps if h.confidence != "weak"}
        for br in topology.blast_radius:
            if br.component_id not in relevant:
                continue
            for i in br.impacts:
                if i.service in affected and IMPACT_ORDER[i.impact] > IMPACT_ORDER.get(
                    impacts.get(i.service, "none"), 0
                ):
                    impacts[i.service] = i.impact
    risks = [
        ServiceRisk(service=s, tier=tiers.get(s), impact=impacts.get(s, "unknown"))
        for s in affected
    ]
    max_imp = max((r.impact for r in risks), key=lambda x: IMPACT_ORDER[x], default="none")
    if affected and all(r.impact == "unknown" for r in risks):
        max_imp = "unknown"

    # Recommendations: policy-filtered draft steps; fall back to rule checks.
    steps, removed = [], []
    source_steps = (
        [(s.step, s.rationale, s.runbook_ids, s.evidence_ids) for s in draft.recommended_steps]
        if draft and draft.recommended_steps
        else [
            (c, f"diagnostic check for {h.hypothesis_id}", [], h.supporting_evidence_ids[:2])
            for h in hyps[:2]
            for c in h.diagnostic_checks
        ]
    )
    deprecated = {e.entity_id for e in evidence.values() if e.data.get("status") == "deprecated"}
    flagged = set(knowledge_flagged(knowledge))
    for text, rationale, runbooks, ev_ids in source_steps:
        decision = check_step(text)
        bad_rb = [r for r in runbooks if r in deprecated or r in flagged]
        if not decision.allowed or bad_rb:
            reason = decision.reason if not decision.allowed else f"relies on {', '.join(bad_rb)}"
            removed.append(f"{text} — {reason}")
            continue
        steps.append(
            DiagnosticStep(
                order=len(steps) + 1,
                step=_clean(text),
                rationale=_clean(rationale),
                runbook_ids=runbooks,
                evidence_ids=[e for e in ev_ids if e in evidence],
            )
        )

    sup_ids = list(dict.fromkeys(i for h in hyps for i in h.supporting_evidence_ids))
    if draft:
        sup_ids += [i for k in draft.key_facts for i in k.evidence_ids if i not in sup_ids]
    con_ids = list(dict.fromkeys(i for h in hyps for i in h.contradicting_evidence_ids))

    hist: dict[str, DocRef] = {}
    for h in hyps:
        for ref in h.historical_references:
            hist.setdefault(
                ref,
                DocRef(
                    doc_id=ref,
                    title=_title(evidence, ref),
                    note=f"similar to {h.hypothesis_id}; context, not proof",
                ),
            )
    if knowledge:
        for r in knowledge.related_incidents:
            hist.setdefault(r.doc_id, DocRef(doc_id=r.doc_id, title=r.title, note=_clean(r.note)))
    runbooks = [
        DocRef(doc_id=r.doc_id, title=r.title, note=_clean(r.note))
        for r in (knowledge.relevant_runbooks if knowledge else [])
        if r.doc_id not in deprecated and r.doc_id not in flagged
    ]

    security_notes = []
    if scope.request_flags:
        security_notes.append(f"request flags: {', '.join(scope.request_flags)}")
    if knowledge and knowledge.suspicious_documents:
        for d in knowledge.suspicious_documents:
            security_notes.append(f"{d.doc_id} quarantined as untrusted: {_clean(d.issue)}")
    security = bool(knowledge and knowledge.suspicious_documents) or (
        "injection_suspected" in scope.request_flags
    )
    escalation = decide_escalation(status, risks, security, anomalies, tiers)

    if draft:
        summary = _clean(draft.summary)
    elif hyps:
        summary = f"Leading hypothesis: {hyps[0].statement}"
    elif not anomalies and telemetry and telemetry.status == "completed":
        summary = "No network-level anomalies were detected in the investigation window."
    else:
        summary = "The investigation could not establish a cause."
    summary = f"[{status.value}] {summary}"[:2500]

    missing = list(verification.missing_information) if verification else []
    unresolved = list(draft.open_questions) if draft else []
    if verification:
        unresolved += [r for r in verification.additional_evidence_requests if r not in unresolved]
    conf = f"Status rule: {why}. " + (
        f"Leading hypothesis {hyps[0].hypothesis_id} is {hyps[0].confidence} — "
        f"{hyps[0].confidence_rationale}"
        if hyps
        else "No hypotheses were produced."
    )
    narrative = (
        draft.risk_and_impact
        if draft
        else f"{len(affected)} services affected; maximum potential impact {max_imp}."
    )
    return InvestigationReport(
        incident_id=scope.investigation_id,
        dataset_id=scope.dataset_id,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status=status,
        investigation_summary=summary,
        facts=[
            _clean(k.statement)
            for k in (draft.key_facts if draft else [])
            if k.evidence_ids and all(e in evidence for e in k.evidence_ids)
        ],
        affected_services=affected,
        affected_network_components=components,
        observed_anomalies=telemetry.anomalies if telemetry else [],
        root_cause_hypotheses=hyps,
        supporting_evidence=[_ref(evidence[i]) for i in sup_ids if i in evidence],
        contradicting_evidence=[_ref(evidence[i]) for i in con_ids if i in evidence],
        historical_incident_references=list(hist.values()),
        relevant_runbooks=runbooks,
        missing_information=missing,
        recommended_diagnostic_steps=steps,
        removed_recommendations=removed,
        risk_and_impact_assessment=RiskAssessment(
            max_impact=max_imp, services=risks, narrative=_clean(narrative)[:1000]
        ),
        confidence_rationale=conf,
        escalation=escalation,
        verification=verification,
        review_concerns=[c.description for c in (review.concerns if review else [])],
        security_notes=security_notes,
        specialist_status={k: v or "missing" for k, v in specialists.items()},
        unresolved_questions=unresolved[:20],
        run_metrics=run_metrics,
    )


def knowledge_flagged(knowledge: KnowledgeFinding | None) -> list[str]:
    if not knowledge:
        return []
    return [d.doc_id for d in knowledge.suspicious_documents]


def _title(evidence: dict[str, Evidence], doc_id: str) -> str:
    for e in evidence.values():
        if e.entity_id == doc_id:
            return e.summary.split(": ", 1)[-1].split(" [flags")[0]
    return doc_id
