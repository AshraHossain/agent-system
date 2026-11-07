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

# Wording that presents an inferred cause as settled fact. A read-only investigation
# ranks hypotheses; it never proves one. A general lexicon of certainty markers (it
# includes the verifier matrix's phrase, so the matrix alone does not measure it); see
# docs/eval_results_verifier.md for what it cannot catch ("The firewall caused this").
CERTAINTY_RE = re.compile(
    r"\b(?:without (?:any |a )?(?:doubt|question)|beyond (?:any |all |a |reasonable )*doubt"
    r"|no (?:doubt|question)(?: that| about it)?|there is no (?:doubt|question)|undoubtedly"
    r"|doubtless|unquestionably|indisputably|undeniably|incontrovertibly|unambiguously"
    r"|conclusively|definitively|definitely|certainly|categorically|absolutely|surely"
    r"|for (?:certain|sure)|with (?:complete |total |full |absolute )?certainty"
    r"|(?:is|was|has been|have been|are|were) (?:now )?(?:confirmed|proven|proved)"
    r"|(?:confirmed|proven|proved) (?:to be|that|as)|it is (?:clear|certain|evident|obvious) that"
    r"|clearly (?:caused|the cause|due to|responsible)|(?:the )?(?:definitive|sole|undisputed) "
    r"(?:root )?cause|root cause (?:is|was)\b)",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:not|no|never|cannot|can't|isn't|wasn't|aren't|nor|yet to)\W+(?:\w+\W+){0,2}$", re.I
)

# Evidence sufficiency, mirroring what the rule engine (core/analysis.py) treats as
# support. A hypothesis must cite an anomalous reading on its own component in a metric
# that fits its category (any metric for `unknown`); congestion may rest on loss or
# latency only when utilization data for the link is missing. Readings the rules count
# against a category must not be cited as its support.
ANOMALOUS = {"elevated", "critical", "recovered"}
SUFFICIENCY_CODES = {
    "insufficient_support",
    "support_contradicts_claim",
    "fact_metric_mismatch",
    "fact_contradicts_evidence",
}
FITTING: dict[HypothesisCategory, set[str] | None] = {
    _C.LINK_CONGESTION: {"utilization_pct"},
    _C.LINK_PHYSICAL: {"error_rate", "packet_loss_pct", "probe_loss_pct"},
    _C.DEVICE_SATURATION: {"cpu_pct", "mem_pct", "session_util_pct"},
    _C.DNS: {"query_latency_ms", "cpu_pct"},
    _C.WAN: {"latency_ms", "packet_loss_pct", "probe_loss_pct"},
    _C.TELEMETRY_ARTIFACT: {"packet_loss_pct"},
    _C.APPLICATION: {"latency_p95_ms", "error_pct"},
    _C.UNKNOWN: None,
}
CONGESTION_WITHOUT_UTILIZATION = {"packet_loss_pct", "probe_loss_pct", "latency_ms"}
AGAINST: dict[HypothesisCategory, set[tuple[str, str]]] = {
    _C.LINK_CONGESTION: {("error_rate", "anomalous"), ("utilization_pct", "normal")},
    _C.LINK_PHYSICAL: {
        ("utilization_pct", "anomalous"),
        ("error_rate", "normal"),
        ("probe_loss_pct", "normal"),
    },
    _C.TELEMETRY_ARTIFACT: {
        ("probe_loss_pct", "anomalous"),
        ("error_rate", "anomalous"),
        ("packet_loss_pct", "normal"),
    },
}
# Metric families a key fact can name, by the words that name them.
FACT_METRICS: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "utilization": ({"utilization_pct"}, ("utiliz", "utilis", "bandwidth")),
    "errors": ({"error_rate", "error_pct"}, ("error", "crc", "fcs")),
    "loss": ({"packet_loss_pct", "probe_loss_pct"}, ("loss",)),
    "latency": ({"latency_ms", "query_latency_ms", "latency_p95_ms"}, ("latency", "rtt")),
    "cpu": ({"cpu_pct"}, ("cpu",)),
    "memory": ({"mem_pct"}, ("memory", "mem_pct")),
    "sessions": ({"session_util_pct"}, ("session",)),
}
# A fact that states one of these describes an abnormal reading.
ABNORMAL_RE = re.compile(
    r"\b(?:critical|elevated|spik\w*|surg\w*|ris(?:e|es|ing|en)|rose|increas\w*"
    r"|saturat\w*|abnormal\w*|anomal\w*|exceed\w*|breach\w*)\b",
    re.IGNORECASE,
)

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


def _reading(ev: Evidence) -> str | None:
    """'anomalous' or 'normal' for a telemetry reading with a verdict, else None."""
    if ev.kind != EvidenceKind.TELEMETRY or not ev.metric:
        return None
    verdict = ev.data.get("verdict")
    return "anomalous" if verdict in ANOMALOUS else "normal" if verdict == "normal" else None


def _missing_metrics(evidence: dict[str, Evidence]) -> set[tuple[str | None, str | None]]:
    """(entity, metric) pairs with missing data; metric None means the whole entity."""
    return {
        (e.entity_id, e.metric) for e in evidence.values() if e.kind == EvidenceKind.DATA_QUALITY
    }


def _sufficiency(
    h: Hypothesis, evidence: dict[str, Evidence], missing: set
) -> tuple[str | None, list[str]]:
    """(what the cited support lacks or None, cited readings that argue against ``h``)."""
    own = {h.component_id, f"svc:{h.component_id}"}
    readings = [
        (e, evidence[e])
        for e in h.supporting_evidence_ids
        if e in evidence and evidence[e].entity_id in own and _reading(evidence[e])
    ]
    against = [
        e for e, ev in readings if (ev.metric, _reading(ev)) in AGAINST.get(h.category, set())
    ]
    fitting = FITTING.get(h.category)
    if h.category == _C.LINK_CONGESTION and (
        (h.component_id, "utilization_pct") in missing or (h.component_id, None) in missing
    ):
        fitting = (fitting or set()) | CONGESTION_WITHOUT_UTILIZATION
    backed = any(
        _reading(ev) == "anomalous" and (fitting is None or ev.metric in fitting)
        for _, ev in readings
    )
    lacking = None
    if not backed and h.confidence != "weak":
        wanted = "/".join(sorted(fitting)) if fitting else "telemetry"
        lacking = f"no anomalous {wanted} reading on {h.component_id}"
    return lacking, against


def _fact_problems(statement: str, cited: list[Evidence]) -> list[tuple[str, str]]:
    """Key fact vs the telemetry readings it cites: right metric, right direction."""
    readings = [ev for ev in cited if _reading(ev)]
    if not readings:
        return []
    text = statement.lower()
    named = set().union(
        *(metrics for metrics, words in FACT_METRICS.values() if any(w in text for w in words))
    )
    out = []
    if named and not any(ev.metric in named for ev in readings):
        measured = ", ".join(sorted({ev.metric for ev in readings}))
        out.append(
            (
                "fact_metric_mismatch",
                f"names a metric its cited readings ({measured}) do not measure",
            )
        )
    relevant = [ev for ev in readings if ev.metric in named] or readings
    claim = next(
        (
            m.group(0)
            for m in ABNORMAL_RE.finditer(statement)
            if not NEGATION_RE.search(statement[max(0, m.start() - 40) : m.start()])
        ),
        None,
    )
    if claim and all(_reading(ev) == "normal" for ev in relevant):
        out.append(
            ("fact_contradicts_evidence", f"says '{claim}' but its cited readings are normal")
        )
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
    missing_data = _missing_metrics(evidence)
    sufficiency_checked = 0
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
        if direct:
            sufficiency_checked += 1
            lacking, against = _sufficiency(h, evidence, missing_data)
            sev = "error" if rank == 0 else "warning"
            if lacking:
                if sev == "error":
                    unsupported.append(f"{h.hypothesis_id}: {lacking}")
                issue(
                    sev,
                    "insufficient_support",
                    f"{h.hypothesis_id} claims {h.category.value} on {h.component_id} but "
                    f"cites {lacking}",
                    [h.hypothesis_id],
                )
            if against:
                if sev == "error":
                    unsupported.append(f"{h.hypothesis_id}: support argues against the claim")
                issue(
                    sev,
                    "support_contradicts_claim",
                    f"{h.hypothesis_id} cites readings that argue against {h.category.value} on "
                    f"{h.component_id}: "
                    + ", ".join(
                        f"{e} ({evidence[e].metric} {evidence[e].data.get('verdict')})"
                        for e in against
                    ),
                    against,
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
            if mentioned:
                sufficiency_checked += 1
            for code, problem in _fact_problems(kf.statement, [evidence[e] for e in mentioned]):
                unsupported.append(f"fact {problem}: {kf.statement[:120]}")
                issue("error", code, f"key fact {problem}: {kf.statement[:80]}", mentioned)
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
            name="evidence_sufficient",
            passed=not any(i.code in SUFFICIENCY_CODES and i.severity == "error" for i in issues),
            details=f"{sufficiency_checked} claims checked against what their readings show",
        )
    )
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


def _certainty_marker(text: str) -> str | None:
    """First certainty phrase in ``text`` that is not negated ("not confirmed")."""
    for m in CERTAINTY_RE.finditer(text):
        if not NEGATION_RE.search(text[max(0, m.start() - 40) : m.start()]):
            return m.group(0)
    return None


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
    if marker := _certainty_marker(
        " ".join([draft.summary, *(f.statement for f in draft.key_facts)])
    ):
        top = hyps[0].confidence if hyps else None
        if top == "strong":
            sev, why = "warning", "even strong evidence ranks a hypothesis; it does not prove it"
        else:
            sev, why = "error", f"the leading hypothesis is only {top or 'absent'}"
        out.append((sev, "overstated_certainty", f"draft states certainty ('{marker}'): {why}"))
    if hyps and hyps[0].component_id.lower() not in text:
        out.append(
            (
                "warning",
                "summary_incomplete",
                f"summary does not name the leading component {hyps[0].component_id}",
            )
        )
    return out
