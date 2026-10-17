"""Deterministic verification of the investigation before the report is finalised.

This is the primary verifier. An LLM reviewer runs afterwards but can only add
concerns; it cannot clear anything found here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from opspilot.contracts.evidence import STATE_PREFIX, Evidence, EvidenceKind, is_evidence_id
from opspilot.contracts.findings import (
    Hypothesis,
    HypothesisCategory,
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
CONF_RANK = {"strong": 0, "moderate": 1, "weak": 2}

# Which component types each hypothesis category can describe. "link" and "service"
# are kinds; every other value is a device type from the topology.
_C = HypothesisCategory
CATEGORY_TARGETS: dict[HypothesisCategory, set[str] | None] = {
    _C.LINK_CONGESTION: {"link"},
    _C.LINK_PHYSICAL: {"link"},
    _C.DEVICE_SATURATION: {
        "router",
        "switch",
        "firewall",
        "load_balancer",
        "dns_server",
        "wan_router",
    },
    _C.DNS: {"dns_server", "service"},
    _C.WAN: {"wan_router", "link"},
    _C.TELEMETRY_ARTIFACT: None,  # any network component
    _C.APPLICATION: {"service"},
    _C.UNKNOWN: None,
}

NO_IMPACT_RE = re.compile(
    r"\bno (?:services?|customers?|users?|applications?) (?:are|is|were|was) "
    r"(?:affected|impacted)\b|\bno (?:customer |service |user )?impact\b"
    r"|\bno action (?:is )?(?:required|needed)\b|\bnothing (?:is )?(?:affected|wrong)\b",
    re.IGNORECASE,
)


def evidence_from_state(state: dict) -> dict[str, Evidence]:
    out = {}
    for key, value in state.items():
        if key.startswith(STATE_PREFIX):
            ev = value if isinstance(value, Evidence) else Evidence.model_validate(value)
            out[ev.evidence_id] = ev
    return out


def _mentions(statement: str, ev: Evidence) -> bool:
    """Does the statement name the entity (or document) the evidence is about?"""
    if not ev.entity_id:
        return True
    names = {ev.entity_id, ev.entity_id.removeprefix("svc:")}
    text = statement.lower()
    return any(n.lower() in text for n in names)


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
    endpoints: dict[str, list[str]] | None = None,
    component_types: dict[str, str] | None = None,
    rule_hypotheses: list[Hypothesis] | None = None,
) -> VerificationResult:
    """``component_types`` maps component/service ids to a type ("link", "service" or a
    device type). ``rule_hypotheses`` are the deterministic rule engine's ranked
    candidates for the same scope; the analyst's confidence and ranking are compared
    with them. Both are optional: without them those checks are skipped."""
    endpoints = endpoints or {}
    component_types = component_types or {}
    irrelevant: list[str] = []
    relevance_checked = 0
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
        # Relevance: direct evidence must concern the hypothesised component, its
        # link endpoints, or the services the hypothesis claims to explain.
        related = {h.component_id, f"svc:{h.component_id}", *endpoints.get(h.component_id, [])}
        related |= {f"svc:{x}" for x in h.explained_services}
        relevance_checked += len(direct)
        off_target = [e for e in direct if evidence[e].entity_id not in related]
        irrelevant += off_target
        if off_target:
            issue(
                "warning",
                "irrelevant_citation",
                f"{h.hypothesis_id} cites evidence about other entities: "
                + ", ".join(f"{e} ({evidence[e].entity_id})" for e in off_target),
                off_target,
            )
        if direct and len(off_target) == len(direct):
            unsupported.append(f"{h.hypothesis_id}: no cited evidence concerns {h.component_id}")
            issue(
                "error",
                "irrelevant_support",
                f"{h.hypothesis_id} ({h.component_id}) cites only evidence about other entities",
                off_target,
            )
        if h.component_id not in known_components and h.component_id not in known_services:
            unsupported.append(f"{h.hypothesis_id}: unknown component {h.component_id}")
            issue(
                "error",
                "unknown_component",
                f"{h.hypothesis_id} names {h.component_id}, which is not in the topology",
            )
        targets = CATEGORY_TARGETS.get(h.category)
        kind = component_types.get(h.component_id)
        if targets is not None and kind is not None and kind not in targets:
            unsupported.append(
                f"{h.hypothesis_id}: {h.category.value} cannot apply to {kind} {h.component_id}"
            )
            issue(
                "error",
                "category_component_mismatch",
                f"{h.hypothesis_id} claims {h.category.value} on {h.component_id}, a {kind}",
                [h.hypothesis_id],
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
    _compare_with_rules(hyps, rule_hypotheses, issue)
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
            valid_ids = [e for e in kf.evidence_ids if e in evidence]
            mentioned = [e for e in valid_ids if _mentions(kf.statement, evidence[e])]
            relevance_checked += len(valid_ids)
            irrelevant += [e for e in valid_ids if e not in mentioned]
            if valid_ids and not mentioned:
                unsupported.append(
                    f"fact cites evidence about something else: {kf.statement[:120]}"
                )
                issue(
                    "error",
                    "fact_citation_mismatch",
                    f"key fact does not concern any cited evidence: {kf.statement[:80]}",
                    valid_ids,
                )
            if not kf.evidence_ids or not all(e in evidence for e in kf.evidence_ids):
                unsupported.append(f"fact without valid evidence: {kf.statement[:120]}")
                issue(
                    "error", "uncited_fact", f"key fact lacks valid evidence: {kf.statement[:80]}"
                )
        for mention in sorted(set(COMPONENT_RE.findall(draft.summary))):
            if mention not in known_components:
                unsupported.append(f"summary mentions unknown component '{mention}'")
                issue("error", "unknown_component_mention", f"summary mentions {mention}")
        claims += 1
        for sev, code, problem in _summary_problems(draft, hyps):
            if sev == "error":
                unsupported.append(problem)
            issue(sev, code, problem)
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
        irrelevant_citations=sorted(set(irrelevant)),
        citations_relevance_checked=relevance_checked,
        verdict=verdict,
    )


def _key(h: Hypothesis) -> tuple[str, str]:
    return (h.category.value, h.component_id)


def _compare_with_rules(hyps: list[Hypothesis], rules: list[Hypothesis] | None, issue) -> None:
    """Analyst confidence and ranking against the deterministic rule engine.

    The analyst may lower a rule's confidence (with reasons) but not raise it, and may
    reorder hypotheses the rules rate equally (a warning) but not promote one the rules
    rate weaker (an error).
    """
    if not rules or not hyps:
        return
    by_key = {_key(r): r for r in rules}
    for h in hyps:
        rule = by_key.get(_key(h))
        if rule and CONF_RANK[h.confidence] < CONF_RANK[rule.confidence]:
            issue(
                "error",
                "confidence_above_rules",
                f"{h.hypothesis_id} is {h.confidence} but the rules support only "
                f"{rule.confidence} for {h.category.value} on {h.component_id}",
                [h.hypothesis_id],
            )
    top, rule_top = hyps[0], rules[0]
    promoted = by_key.get(_key(top))
    if _key(top) == _key(rule_top) or promoted is None:
        return
    weaker = CONF_RANK[promoted.confidence] > CONF_RANK[rule_top.confidence]
    issue(
        "error" if weaker else "warning",
        "ranking_differs_from_rules",
        f"top hypothesis {top.category.value} on {top.component_id} is ranked below "
        f"{rule_top.category.value} on {rule_top.component_id} by the rules"
        + (f" ({promoted.confidence} vs {rule_top.confidence})" if weaker else " (tie)"),
        [top.hypothesis_id],
    )


def _summary_problems(draft: ReportDraft, hyps: list[Hypothesis]) -> list[tuple[str, str, str]]:
    """The summary must agree with the draft's own structured findings.

    Claiming no impact while services are affected is a contradiction (error).
    Omitting the affected services or the leading component is incomplete (warning):
    visible, but a terse summary is not by itself wrong.
    """
    text = draft.summary.lower()
    out: list[tuple[str, str, str]] = []
    if draft.affected_services and NO_IMPACT_RE.search(draft.summary):
        out.append(
            (
                "error",
                "summary_contradicts_findings",
                "summary claims no impact while the draft lists affected services: "
                + ", ".join(draft.affected_services),
            )
        )
    if draft.affected_services and not any(s.lower() in text for s in draft.affected_services):
        out.append(("warning", "summary_incomplete", "summary names none of the affected services"))
    if hyps and hyps[0].component_id.lower() not in text:
        out.append(
            (
                "warning",
                "summary_incomplete",
                f"summary does not name the leading component {hyps[0].component_id}",
            )
        )
    return out
