"""Deterministic verification of the investigation before the report is finalised.

This is the primary verifier. An LLM reviewer runs afterwards but can only add
concerns; it cannot clear anything found here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from opspilot.contracts.evidence import STATE_PREFIX, Evidence, EvidenceKind, is_evidence_id
from opspilot.contracts.findings import (
    IncidentAnalysis,
    KnowledgeFinding,
    ReportDraft,
    TelemetryFinding,
    TopologyFinding,
)
from opspilot.contracts.verification import Check, VerificationIssue, VerificationResult
from opspilot.core.policy import check_step
from opspilot.core.security import contains_secret, detect_injection

COMPONENT_RE = re.compile(
    r"\b(lnk-[a-z0-9-]+|leaf-\d+|spine-\d+|core-\d+|fw-\d+|lb-\d+|dns-\d+|wan-\d+)\b"
)
DIRECT_KINDS = {EvidenceKind.TELEMETRY, EvidenceKind.TOPOLOGY, EvidenceKind.DATA_QUALITY}


def evidence_from_state(state: dict) -> dict[str, Evidence]:
    out = {}
    for key, value in state.items():
        if key.startswith(STATE_PREFIX):
            ev = value if isinstance(value, Evidence) else Evidence.model_validate(value)
            out[ev.evidence_id] = ev
    return out


def _ids(*groups: Iterable[str]) -> list[str]:
    out: list[str] = []
    for g in groups:
        out.extend(g)
    return out


def verify(
    *,
    evidence: dict[str, Evidence],
    telemetry: TelemetryFinding | None,
    topology: TopologyFinding | None,
    knowledge: KnowledgeFinding | None,
    analysis: IncidentAnalysis | None,
    draft: ReportDraft | None,
    known_components: set[str],
    known_services: set[str],
    suspicious_docs: set[str],
) -> VerificationResult:
    issues: list[VerificationIssue] = []
    checks: list[Check] = []
    unsupported: list[str] = []
    contradictions: list[str] = []
    violations: list[str] = []
    missing: list[str] = []
    requests: list[str] = []
    claims = 0

    def issue(sev, code, msg, refs=()):
        issues.append(VerificationIssue(severity=sev, code=code, message=msg, refs=list(refs)))

    # 1. Citation existence ------------------------------------------------
    cited: list[str] = []
    for f in (telemetry, topology, knowledge):
        if f is not None:
            cited += f.evidence_ids
    if telemetry:
        for a in telemetry.anomalies:
            cited += a.evidence_ids
    if analysis:
        for h in analysis.hypotheses:
            cited += h.supporting_evidence_ids + h.contradicting_evidence_ids
    if draft:
        for kf in draft.key_facts:
            cited += kf.evidence_ids
        for st in draft.recommended_steps:
            cited += st.evidence_ids
    invalid = sorted({c for c in cited if not is_evidence_id(c) or c not in evidence})
    for c in invalid:
        issue("error", "invalid_citation", f"cited evidence {c} does not exist", [c])
    checks.append(
        Check(
            name="citations_exist",
            passed=not invalid,
            details=f"{len(set(cited)) - len(invalid)}/{len(set(cited))} cited IDs exist",
        )
    )

    # 2. Hypothesis support -------------------------------------------------
    hyps = analysis.hypotheses if analysis else []
    for rank, h in enumerate(hyps):
        claims += 1
        direct = [
            e
            for e in h.supporting_evidence_ids
            if e in evidence and evidence[e].kind in DIRECT_KINDS
        ]
        if not direct:
            sev = "error" if rank == 0 else "warning"
            unsupported.append(f"{h.hypothesis_id}: {h.statement[:120]}")
            issue(
                sev,
                "unsupported_hypothesis",
                f"{h.hypothesis_id} has no direct (telemetry/topology) supporting evidence",
                [h.hypothesis_id],
            )
        related = {h.component_id, f"svc:{h.component_id}"}
        off_target = [
            e
            for e in direct
            if evidence[e].kind == EvidenceKind.TELEMETRY and evidence[e].entity_id not in related
        ]
        if off_target and len(off_target) == len(direct):
            issue(
                "warning",
                "evidence_off_target",
                f"{h.hypothesis_id} cites telemetry about other entities only",
                off_target,
            )
        if h.component_id not in known_components and h.component_id not in known_services:
            unsupported.append(f"{h.hypothesis_id}: unknown component {h.component_id}")
            issue(
                "error",
                "unknown_component",
                f"{h.hypothesis_id} names {h.component_id}, which is not in the topology",
            )
        doc_support = [
            e
            for e in h.supporting_evidence_ids
            if e in evidence and evidence[e].entity_id in suspicious_docs
        ]
        if doc_support:
            issue(
                "error",
                "suspicious_document_cited",
                f"{h.hypothesis_id} relies on a quarantined document",
                doc_support,
            )
        if rank < 2 and h.contradicting_evidence_ids:
            contradictions.append(
                f"{h.hypothesis_id} ({h.category.value} on {h.component_id}) is contradicted by "
                f"{', '.join(h.contradicting_evidence_ids)}"
            )
    if hyps:
        top = hyps[0]
        for other in hyps[1:]:
            if other.component_id == top.component_id and other.contradicting_evidence_ids:
                contradictions.append(
                    f"competing explanations for {top.component_id}: {top.category.value} vs "
                    f"{other.category.value} (contradicting measurements present)"
                )
    for c in contradictions:
        issue("warning", "contradiction", c)
    checks.append(
        Check(
            name="hypotheses_supported",
            passed=not any(
                i.code == "unsupported_hypothesis" and i.severity == "error" for i in issues
            ),
            details=f"{len(hyps)} hypotheses checked",
        )
    )

    # 3. Draft claims ⊆ specialist findings ----------------------------------
    if draft:
        supported_services = set()
        if telemetry:
            supported_services |= set(telemetry.affected_services)
        for h in hyps:
            supported_services |= set(h.explained_services)
        supported_components = {h.component_id for h in hyps}
        if telemetry:
            supported_components |= set(telemetry.affected_entities)
        if topology:
            supported_components |= {s.component_id for s in topology.shared_dependencies}
            supported_components |= {b.component_id for b in topology.blast_radius}
        for s in draft.affected_services:
            claims += 1
            if s not in supported_services:
                unsupported.append(f"affected service '{s}' is not supported by any finding")
                issue(
                    "error", "unsupported_service", f"draft claims {s} is affected without support"
                )
        for comp in draft.affected_components:
            claims += 1
            if comp not in supported_components:
                unsupported.append(f"affected component '{comp}' is not supported by any finding")
                issue(
                    "error",
                    "unsupported_component",
                    f"draft claims {comp} is affected without support",
                )
        for kf in draft.key_facts:
            claims += 1
            if not kf.evidence_ids or not all(e in evidence for e in kf.evidence_ids):
                unsupported.append(f"fact without valid evidence: {kf.statement[:120]}")
                issue(
                    "error", "uncited_fact", f"key fact lacks valid evidence: {kf.statement[:80]}"
                )
        for mention in sorted(set(COMPONENT_RE.findall(draft.summary))):
            if mention not in known_components:
                unsupported.append(f"summary mentions unknown component '{mention}'")
                issue("error", "unknown_component_mention", f"summary mentions {mention}")
    checks.append(
        Check(
            name="draft_claims_supported",
            passed=not any(
                i.code.startswith(("unsupported_", "uncited", "unknown_")) and i.severity == "error"
                for i in issues
            ),
            details=f"{claims} claims checked",
        )
    )

    # 4. Read-only policy ---------------------------------------------------
    steps = [s.step for s in draft.recommended_steps] if draft else []
    steps += [c for h in hyps for c in h.diagnostic_checks]
    for s in steps:
        claims += 1
        d = check_step(s)
        if not d.allowed:
            violations.append(f"{s[:160]} — {d.reason}")
            issue(
                "error", "policy_violation", f"recommendation violates read-only policy: {d.reason}"
            )
    if draft:
        for st in draft.recommended_steps:
            for rb in st.runbook_ids:
                ev = next((e for e in evidence.values() if e.entity_id == rb), None)
                if ev is not None and ev.data.get("status") == "deprecated":
                    issue(
                        "error",
                        "deprecated_runbook",
                        f"step relies on deprecated runbook {rb}",
                        [rb],
                    )
                if rb in suspicious_docs:
                    issue(
                        "error",
                        "suspicious_document_cited",
                        f"step relies on quarantined {rb}",
                        [rb],
                    )
    checks.append(
        Check(name="read_only_policy", passed=not violations, details=f"{len(steps)} steps checked")
    )

    # 5. Output safety -------------------------------------------------------
    texts = []
    if draft:
        texts += [
            draft.summary,
            draft.risk_and_impact,
            *draft.open_questions,
            *(k.statement for k in draft.key_facts),
            *steps,
        ]
    texts += [h.statement for h in hyps]
    leaked = [t for t in texts if contains_secret(t)]
    injected = [t for t in texts if detect_injection(t)]
    for t in leaked:
        issue("error", "secret_in_output", f"output contains a secret-like value: {t[:60]}…")
    for t in injected:
        issue("error", "injection_in_output", f"output echoes injection-like text: {t[:60]}…")
    checks.append(Check(name="output_safety", passed=not (leaked or injected)))

    # 6. Missing information --------------------------------------------------
    if telemetry:
        missing += telemetry.data_gaps
    for e in evidence.values():
        if e.kind == EvidenceKind.DATA_QUALITY and e.summary not in missing:
            missing.append(e.summary)
    if topology:
        missing += [u for u in topology.uncertainties if u not in missing]
    if analysis:
        missing += [u for u in analysis.unexplained_observations if u not in missing]
    for name, f in (
        ("telemetry", telemetry),
        ("topology", topology),
        ("knowledge", knowledge),
        ("incident analysis", analysis),
    ):
        if f is None:
            missing.append(f"{name} findings are missing")
            requests.append(f"Re-run {name}")
        elif f.status != "completed":
            missing.append(f"{name} stage {f.status}: {'; '.join(f.errors)[:200]}")
            requests.append(f"Re-run {name} once its data sources are available")
    for e in evidence.values():
        if e.kind == EvidenceKind.DATA_QUALITY:
            requests.append(f"Collect missing/delayed data: {e.summary}")
    checks.append(
        Check(name="missing_information_reported", passed=True, details=f"{len(missing)} items")
    )

    errors = [i for i in issues if i.severity == "error"]
    if analysis is None and draft is None:
        verdict = "fail"
    elif errors or contradictions:
        verdict = "needs_review"
    else:
        verdict = "pass"
    return VerificationResult(
        checks=checks,
        issues=issues,
        invalid_citations=invalid,
        unsupported_claims=unsupported,
        contradictions=contradictions,
        policy_violations=violations,
        missing_information=list(dict.fromkeys(missing)),
        additional_evidence_requests=list(dict.fromkeys(requests)),
        claims_checked=claims,
        verdict=verdict,
    )
