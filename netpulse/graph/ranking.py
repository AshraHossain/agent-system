"""Deterministic hypothesis ranking with ordinal confidence (ADR-0007).

Confidence never comes from the generator. It is computed from what the
evidence registry actually contains.

| Level | Rule |
|---|---|
| insufficient_evidence | no trusted evidence is cited |
| low | trusted evidence exists, but: no direct observation of the root, incomplete root |
|     | telemetry, or contradicting evidence cited |
| medium | ≥ 2 trusted items from ≥ 2 sources, with the root observed directly |
| high | ≥ 3 trusted items from ≥ 2 sources, with the root observed directly |

``evidence_sufficient`` requires the top hypothesis to be medium or high.
``conclusive`` additionally requires the runner-up to rank strictly lower,
unless the top-level hypotheses are complementary (distinct roots that the
topology's minimal cover needs together, i.e. multiple simultaneous faults).
"""

from __future__ import annotations

from netpulse.models import (
    Anomaly,
    ConfidenceAssessment,
    ConfidenceLevel,
    EvidenceItem,
    EvidenceSource,
    Hypothesis,
    RankedHypothesis,
    RootCauseCategory,
)

_ORDER = {
    ConfidenceLevel.HIGH: 3,
    ConfidenceLevel.MEDIUM: 2,
    ConfidenceLevel.LOW: 1,
    ConfidenceLevel.INSUFFICIENT: 0,
}
_CONTEXT_SOURCES = {
    RootCauseCategory.MAINTENANCE_SIDE_EFFECT: EvidenceSource.MAINTENANCE,
    RootCauseCategory.CONFIGURATION_CHANGE: EvidenceSource.EVENT_LOG,
}


def _root_observed(h: Hypothesis, cited: list[EvidenceItem], anomalies: list[Anomaly]) -> bool:
    root = h.suspected_root_entity
    if root is None:
        return False
    context_source = _CONTEXT_SOURCES.get(h.cause_category)
    if context_source and any(e.source == context_source and root in e.entity_ids for e in cited):
        return True
    return any(a.confirmed and a.entity_id == root for a in anomalies)


def assess(
    hypotheses: list[Hypothesis],
    registry: dict[str, EvidenceItem],
    anomalies: list[Anomaly],
    coverage_gaps: list[str],
    minimal_cover: list[str],
) -> ConfidenceAssessment:
    ranked: list[tuple[int, int, str, RankedHypothesis, Hypothesis]] = []
    for h in hypotheses:
        cited = [registry[e] for e in h.supporting_evidence if e in registry]
        trusted = [e for e in cited if e.trusted]
        sources = {e.source for e in trusted}
        contradictions = [e for e in h.contradicting_evidence if e in registry]
        observed = _root_observed(h, cited, anomalies)
        gap = h.suspected_root_entity in coverage_gaps
        if not trusted:
            level, why = ConfidenceLevel.INSUFFICIENT, "no trusted evidence cited"
        elif contradictions:
            level, why = ConfidenceLevel.LOW, f"{len(contradictions)} contradicting evidence item(s)"
        elif not observed:
            level, why = ConfidenceLevel.LOW, "root entity has no observation of its own"
        elif gap:
            level, why = ConfidenceLevel.LOW, "root entity telemetry is incomplete"
        elif len(trusted) >= 3 and len(sources) >= 2:
            level, why = ConfidenceLevel.HIGH, f"{len(trusted)} trusted items from {len(sources)} sources"
        elif len(trusted) >= 2 and len(sources) >= 2:
            level, why = ConfidenceLevel.MEDIUM, f"{len(trusted)} trusted items from {len(sources)} sources"
        else:
            level, why = ConfidenceLevel.LOW, f"only {len(trusted)} trusted item(s) from {len(sources)} source(s)"
        item = RankedHypothesis(
            hypothesis_id=h.hypothesis_id,
            rank=1,
            confidence=level,
            trusted_support_count=len(trusted),
            contradiction_count=len(contradictions),
            rationale=f"{level.value}: {why}; cites " + ", ".join(e.evidence_id for e in trusted[:6]),
        )
        ranked.append((-_ORDER[level], -len(trusted), h.hypothesis_id, item, h))
    ranked.sort(key=lambda r: r[:3])
    ranking = [r[3].model_copy(update={"rank": i}) for i, r in enumerate(ranked, start=1)]

    if not ranking:
        return ConfidenceAssessment(
            overall=ConfidenceLevel.INSUFFICIENT,
            evidence_sufficient=False,
            conclusive=False,
            missing_evidence=["no confirmed anomaly or hypothesis to evaluate"],
            rationale="No hypotheses were produced; nothing can be concluded.",
        )
    top = ranking[0]
    sufficient = top.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)
    peers = [r for r in ranked if _ORDER[r[3].confidence] == _ORDER[top.confidence]]
    complementary = (
        len(peers) > 1
        and all(
            r[4].suspected_root_entity in minimal_cover or set(r[4].affected_components) & set(minimal_cover)
            for r in peers
        )
        and len({r[4].suspected_root_entity for r in peers}) == len(peers)
    )
    conclusive = sufficient and (len(peers) == 1 or complementary)
    missing = []
    for _, _, _, _, h in ranked[:3]:
        missing += [m for m in h.missing_evidence if m not in missing]
    missing += [
        f"telemetry for {g} is incomplete" for g in coverage_gaps if f"telemetry for {g} is incomplete" not in missing
    ]
    rationale = (
        f"Top hypothesis {top.hypothesis_id} is {top.confidence.value}."
        + (" Multiple complementary causes." if complementary and conclusive else "")
        + ("" if conclusive else " Evidence does not single out a cause.")
    )
    return ConfidenceAssessment(
        overall=top.confidence,
        evidence_sufficient=sufficient,
        conclusive=conclusive,
        ranking=ranking,
        missing_evidence=missing[:10],
        rationale=rationale,
    )
