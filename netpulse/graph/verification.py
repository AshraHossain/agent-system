"""Deterministic evidence verification of generated hypotheses.

Every check is code, never an LLM. A hypothesis is **rejected** if any
*blocking* issue applies:

| Code | Rule |
|---|---|
| unknown_evidence_ref | cites an ID that is not in the evidence registry |
| no_supporting_evidence | no resolvable supporting evidence |
| untrusted_only_support | all supporting evidence is untrusted (runbooks, past incidents) |
| unknown_entity | names an entity that does not exist in the topology |
| entity_not_in_evidence | the suspected root appears in none of its cited evidence |
| numeric_claim_unsupported | states a number that appears in none of its cited evidence |
| contradicted_by_telemetry | category needs a detector anomaly on the root; root is observed; none found |
| missing_required_context | maintenance / config-change hypothesis without a matching record or event |
| no_hypotheses | the generator returned nothing although confirmed anomalies exist |

``duplicate_hypothesis`` (same category and root as an earlier one) is
non-blocking. The duplicate is dropped, and no retry is triggered for it.

Contradictions are recorded as new ``ev-chk-*`` evidence ("no CPU anomaly on
core-2 although telemetry is complete"). The rejection is therefore itself
citable, and an LLM explanation can never override telemetry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from netpulse.evidence import EvidenceIdAllocator
from netpulse.models import (
    Anomaly,
    EvidenceItem,
    EvidenceSource,
    Hypothesis,
    Metric,
    RootCauseCategory,
    VerificationCode,
    VerificationIssue,
    VerificationResult,
)
from netpulse.topology.analysis import TopologyGraph

# category -> (required root kind, metrics of which a confirmed anomaly must exist on the root)
REQUIREMENTS: dict[RootCauseCategory, tuple[str, set[Metric], str]] = {
    RootCauseCategory.DEVICE_CPU_SATURATION: ("node", {Metric.CPU_PCT}, "high"),
    RootCauseCategory.DEVICE_MEMORY_EXHAUSTION: ("node", {Metric.MEMORY_PCT}, "high"),
    RootCauseCategory.LINK_CONGESTION: ("link", {Metric.UTILIZATION_PCT}, "high"),
    RootCauseCategory.TRAFFIC_SURGE: ("link", {Metric.UTILIZATION_PCT}, "high"),
    RootCauseCategory.LINK_DEGRADATION: ("link", {Metric.PACKET_LOSS_PCT, Metric.ERROR_RATE}, "high"),
}
CONTEXT_REQUIREMENTS = {
    RootCauseCategory.MAINTENANCE_SIDE_EFFECT: (EvidenceSource.MAINTENANCE, None),
    RootCauseCategory.CONFIGURATION_CHANGE: (EvidenceSource.EVENT_LOG, "config_change"),
}

_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9_]*(?:-[a-z0-9_]+)+\b", re.I)  # core-1, link-a-b, ev-anom-0001, hyp-01
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
SMALL_COUNT = 10  # plain integers below this are treated as counts ("two links", "3 services")


@dataclass
class VerificationOutcome:
    result: VerificationResult
    feedback: list[str]
    new_evidence: dict[str, EvidenceItem] = field(default_factory=dict)


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER.findall(_IDENTIFIER.sub(" ", text))}


def verify(
    hypotheses: list[Hypothesis],
    registry: dict[str, EvidenceItem],
    anomalies: list[Anomaly],
    graph: TopologyGraph,
    coverage_gaps: list[str],
    observed_entities: list[str],
    attempt: int,
) -> VerificationOutcome:
    alloc = EvidenceIdAllocator(registry)
    known = graph.topology.entity_ids()
    confirmed = [a for a in anomalies if a.confirmed]
    issues: list[VerificationIssue] = []
    new_evidence: dict[str, EvidenceItem] = {}
    accepted, rejected, seen = [], [], set()

    def issue(
        code: VerificationCode,
        detail: str,
        hyp: Hypothesis | None,
        evidence_id: str | None = None,
        blocking: bool = True,
    ) -> None:
        issues.append(
            VerificationIssue(
                code=code,
                detail=detail[:500],
                blocking=blocking,
                hypothesis_id=hyp.hypothesis_id if hyp else None,
                evidence_id=evidence_id,
            )
        )

    if not hypotheses and confirmed:
        issue(VerificationCode.NO_HYPOTHESES, f"{len(confirmed)} confirmed anomalies but no hypotheses", None)

    for h in hypotheses:
        before = sum(i.blocking for i in issues)
        cited_ids = h.supporting_evidence + h.contradicting_evidence
        for eid in dict.fromkeys(cited_ids):
            if eid not in registry:
                issue(VerificationCode.UNKNOWN_EVIDENCE_REF, f"{eid} is not in the evidence registry", h, eid)
        support = [registry[e] for e in h.supporting_evidence if e in registry]
        if not support:
            issue(VerificationCode.NO_SUPPORTING_EVIDENCE, "no resolvable supporting evidence", h)
        elif not any(e.trusted for e in support):
            issue(
                VerificationCode.UNTRUSTED_ONLY_SUPPORT,
                "supported only by untrusted documents (runbooks/past incidents)",
                h,
            )

        root = h.suspected_root_entity
        for entity in dict.fromkeys(([root] if root else []) + h.affected_components):
            if entity not in known:
                issue(VerificationCode.UNKNOWN_ENTITY, f"{entity!r} does not exist in the topology", h)
        if root and root in known and support:
            cited_entities = graph.expand([e for item in support for e in item.entity_ids if e in known])
            if root not in cited_entities:
                issue(VerificationCode.ENTITY_NOT_IN_EVIDENCE, f"{root} appears in none of the cited evidence", h)

        allowed = {n for e in support for n in _numbers(e.summary)}
        for value in sorted(_numbers(f"{h.description} {h.confidence_rationale}")):
            if value not in allowed and not (value.is_integer() and value < SMALL_COUNT):
                issue(
                    VerificationCode.NUMERIC_CLAIM_UNSUPPORTED,
                    f"states {value:g}, which none of its cited evidence contains",
                    h,
                )

        if root and root in known:
            _check_category(
                h, root, graph, confirmed, coverage_gaps, observed_entities, registry, new_evidence, alloc, issue
            )

        key = (h.cause_category, root)
        if sum(i.blocking for i in issues) > before:
            rejected.append(h.hypothesis_id)
        elif key in seen:
            issue(
                VerificationCode.DUPLICATE_HYPOTHESIS,
                "same category and root as an earlier hypothesis",
                h,
                blocking=False,
            )
            rejected.append(h.hypothesis_id)
        else:
            seen.add(key)
            accepted.append(h.hypothesis_id)

    blocking = [i for i in issues if i.blocking]
    feedback = [f"{i.hypothesis_id or 'output'}: {i.code.value}: {i.detail}" for i in blocking][:15]
    result = VerificationResult(
        attempt=attempt,
        passed=not blocking,
        issues=issues,
        accepted_hypothesis_ids=accepted,
        rejected_hypothesis_ids=rejected,
    )
    return VerificationOutcome(result=result, feedback=feedback, new_evidence=new_evidence)


def _check_category(h, root, graph, confirmed, coverage_gaps, observed_entities, registry, new_evidence, alloc, issue):
    category = h.cause_category
    if category in CONTEXT_REQUIREMENTS:
        source, marker = CONTEXT_REQUIREMENTS[category]
        scope = graph.expand([root])
        found = [
            e
            for e in registry.values()
            if e.source == source and graph.expand(e.entity_ids) & scope and (marker is None or marker in e.summary)
        ]
        if not found:
            issue(
                VerificationCode.MISSING_REQUIRED_CONTEXT,
                f"{category.value} requires a {source.value} record touching {root}; none exists",
                h,
            )
        return
    if category == RootCauseCategory.TELEMETRY_FAULT:
        if root not in coverage_gaps and not any(a.entity_id == root and a.implausible for a in confirmed):
            issue(
                VerificationCode.CONTRADICTED_BY_TELEMETRY,
                f"telemetry_fault requires implausible readings or a gap on {root}; neither exists",
                h,
            )
        return
    if category not in REQUIREMENTS:
        return
    kind, metrics, direction = REQUIREMENTS[category]
    actual_kind = graph.topology.kind(root)
    if actual_kind == "node" and kind == "link":
        candidates = {link_id for _, link_id in graph.adjacency[root]}  # a node may be named for a link symptom
    elif actual_kind != kind:
        issue(
            VerificationCode.CONTRADICTED_BY_TELEMETRY, f"{category.value} applies to a {kind}, not a {actual_kind}", h
        )
        return
    else:
        candidates = {root}
    if any(a.entity_id in candidates and a.metric in metrics and a.direction == direction for a in confirmed):
        return
    if root in coverage_gaps or root not in observed_entities:
        return  # cannot contradict what was not observed; ranking caps such hypotheses at low
    family = "/".join(sorted(m.value for m in metrics))
    ref = f"chk:{root}:{family}"
    existing = next((e for e in list(registry.values()) + list(new_evidence.values()) if e.source_ref == ref), None)
    if existing is None:
        existing = EvidenceItem(
            evidence_id=alloc.next("chk"),
            source=EvidenceSource.DETECTOR,
            summary=f"Verifier check: no confirmed {direction} {family} anomaly on {root} in the investigation "
            "window, although its telemetry coverage is adequate.",
            entity_ids=[root],
            method="verifier.negative_check",
            source_ref=ref,
        )
        new_evidence[existing.evidence_id] = existing
    issue(
        VerificationCode.CONTRADICTED_BY_TELEMETRY,
        f"{category.value} at {root} is contradicted: no confirmed {family} anomaly ({existing.evidence_id})",
        h,
        existing.evidence_id,
    )
