"""Rule-based hypothesis generator: the no-LLM baseline and CI default.

It maps patterns in deterministic evidence to the root-cause taxonomy. It
is written against fault *classes* (what a CPU saturation or a link
degradation looks like), not against specific evaluation cases. It is
reported as a baseline, never as the product.

Rules are applied per entity in the topology's minimal cover. Each entity
there has an anomaly of its own and jointly they explain the symptoms. When
no candidate is corroborated, the generator only emits low-specificity
hypotheses that point at visibility gaps (unmonitored or missing-telemetry
entities). It never invents a localized cause.
"""

from __future__ import annotations

from collections import defaultdict

from netpulse.llm.base import EvidenceView, GenerationContext, GenerationResult
from netpulse.models import Anomaly, EvidenceSource, Hypothesis, Metric, RootCauseCategory
from netpulse.policy.catalog import load_catalog
from netpulse.topology.analysis import TopologyGraph

LINK_HEALTH = {Metric.PACKET_LOSS_PCT, Metric.ERROR_RATE}
MAX_HYPOTHESES = 5


class HeuristicGenerator:
    provider = "heuristic"
    model = None

    def __init__(self, graph: TopologyGraph) -> None:
        self.graph = graph
        self.catalog = load_catalog()

    # ------------------------------------------------------------- helpers

    def _scope(self, entity: str) -> set[str]:
        """The entity, its link endpoints, and for nodes their attached links."""
        scope = self.graph.expand([entity])
        if self.graph.topology.kind(entity) == "node":
            scope |= {link_id for _, link_id in self.graph.adjacency[entity]}
        return scope

    def _evidence_on(self, ctx: GenerationContext, source: EvidenceSource, entities: set[str]) -> list[EvidenceView]:
        """Evidence of ``source`` touching the entities, matching links by shared endpoints."""
        return [e for e in ctx.evidence if e.source == source and self.graph.expand(e.entity_ids) & entities]

    def _validation_steps(self, category: RootCauseCategory) -> list[str]:
        return [
            f"{a.catalog_id}: {a.description}"
            for a in self.catalog.values()
            if a.kind == "diagnostic" and category.value in a.applies_to
        ][:3]

    def _classify(
        self, ctx: GenerationContext, root: str, own: list[Anomaly]
    ) -> tuple[RootCauseCategory, str, list[EvidenceView]]:
        """Return (category, root entity, context evidence) for one corroborated candidate."""
        scope = self._scope(root)
        maintenance = self._evidence_on(ctx, EvidenceSource.MAINTENANCE, scope)
        if any(a.implausible for a in own):
            return RootCauseCategory.TELEMETRY_FAULT, root, []
        if maintenance:
            return RootCauseCategory.MAINTENANCE_SIDE_EFFECT, maintenance[0].entity_ids[0], maintenance
        changes = [e for e in self._evidence_on(ctx, EvidenceSource.EVENT_LOG, scope) if "config_change" in e.summary]
        if changes:
            return RootCauseCategory.CONFIGURATION_CHANGE, changes[0].entity_ids[0], changes
        metrics = {a.metric for a in own}
        if Metric.CPU_PCT in metrics:
            return RootCauseCategory.DEVICE_CPU_SATURATION, root, []
        if Metric.MEMORY_PCT in metrics:
            return RootCauseCategory.DEVICE_MEMORY_EXHAUSTION, root, []
        if metrics & LINK_HEALTH and Metric.UTILIZATION_PCT not in metrics:
            return RootCauseCategory.LINK_DEGRADATION, root, []
        if Metric.UTILIZATION_PCT in metrics:
            surging = {
                a.entity_id
                for a in ctx.anomalies
                if a.confirmed and a.metric == Metric.UTILIZATION_PCT and a.direction == "high"
            }
            if len(surging) >= 2:
                return RootCauseCategory.TRAFFIC_SURGE, root, []
            return RootCauseCategory.LINK_CONGESTION, root, []
        return RootCauseCategory.UNKNOWN, root, []

    # ---------------------------------------------------------------- main

    def generate(self, ctx: GenerationContext) -> GenerationResult:
        loc = ctx.localization
        confirmed = [a for a in ctx.anomalies if a.confirmed]
        if not confirmed or loc is None:
            return GenerationResult(hypotheses=[], provider=self.provider)
        if not loc.corroborated:
            return GenerationResult(hypotheses=self._uncorroborated(ctx), provider=self.provider)

        by_entity: dict[str, list[Anomaly]] = defaultdict(list)
        for a in confirmed:
            by_entity[a.entity_id].append(a)
        localization_ids = [e.evidence_id for e in ctx.evidence if e.source == EvidenceSource.TOPOLOGY]
        candidates = {c.entity_id: c for c in loc.candidates}

        drafts: dict[tuple[str, str], dict] = {}
        for root in loc.minimal_cover:
            category, entity, context_evidence = self._classify(ctx, root, by_entity.get(root, []))
            explained = candidates[root].explains if root in candidates else [root]
            # A surge spans several links; merge them into one hypothesis rather than one per link.
            key = (category.value, "*" if category == RootCauseCategory.TRAFFIC_SURGE else entity)
            draft = drafts.setdefault(
                key, {"category": category, "entity": entity, "explained": set(), "support": set(), "roots": set()}
            )
            draft["roots"].add(root)
            draft["explained"].update(explained)
            for symptom in [root, *explained]:
                draft["support"].update(a.evidence_id for a in by_entity.get(symptom, []))
            draft["support"].update(e.evidence_id for e in context_evidence)
            draft["support"].update(localization_ids)

        hypotheses = [self._finish(ctx, n, d, loc) for n, d in enumerate(drafts.values(), start=1)]
        return GenerationResult(hypotheses=hypotheses[:MAX_HYPOTHESES], provider=self.provider)

    def _finish(self, ctx: GenerationContext, n: int, draft: dict, loc) -> Hypothesis:
        category: RootCauseCategory = draft["category"]
        entity: str = draft["entity"]
        alternatives = [
            f"{c.entity_id} (explains {len(c.explains)} of {len(loc.symptoms)} symptoms)"
            for c in loc.candidates
            if c.entity_id not in draft["roots"]
        ][:3]
        missing = [f"telemetry for {g} is incomplete in the window" for g in ctx.coverage_gaps]
        if category == RootCauseCategory.DEVICE_CPU_SATURATION:
            missing.append("process-level CPU breakdown on the device")
        if category == RootCauseCategory.LINK_DEGRADATION:
            missing.append("optical power readings for the link")
        trusted = [e for e in ctx.evidence if e.evidence_id in draft["support"] and e.trusted]
        return Hypothesis(
            hypothesis_id=f"hyp-{n:02d}",
            description=(
                f"Inferred cause: {category.value.replace('_', ' ')} at {entity}, which would explain anomalies on "
                f"{', '.join(sorted(draft['explained']))}."
            )[:800],
            cause_category=category,
            suspected_root_entity=entity,
            affected_components=sorted(draft["explained"] | draft["roots"]),
            supporting_evidence=sorted(draft["support"]),
            contradicting_evidence=[],
            alternative_explanations=alternatives,
            missing_evidence=missing,
            validation_steps=self._validation_steps(category),
            confidence_rationale=(
                f"Rule-based: {len(trusted)} trusted evidence items from "
                f"{len({e.source for e in trusted})} sources; the root entity shows its own detector anomaly. "
                "Temporal and structural association only; causation is not proven."
            ),
            generated_by="heuristic",
        )

    def _uncorroborated(self, ctx: GenerationContext) -> list[Hypothesis]:
        """No candidate shows its own anomaly: point at visibility gaps, never at a guessed cause."""
        loc = ctx.localization
        symptoms_ev = sorted(a.evidence_id for a in ctx.anomalies if a.confirmed)
        topo_ev = [e.evidence_id for e in ctx.evidence if e.source == EvidenceSource.TOPOLOGY]
        out = []
        for cand in loc.candidates:
            if cand.monitored and cand.entity_id not in ctx.coverage_gaps:
                continue
            unmonitored = not cand.monitored
            out.append(
                Hypothesis(
                    hypothesis_id=f"hyp-{len(out) + 1:02d}",
                    description=(
                        f"Possible fault at {cand.entity_id}, which lies on the path of the affected service(s) but "
                        + ("is not monitored" if unmonitored else "has missing telemetry")
                        + "; it cannot be confirmed from available evidence."
                    ),
                    cause_category=(
                        RootCauseCategory.UPSTREAM_DEPENDENCY if unmonitored else RootCauseCategory.UNKNOWN
                    ),
                    suspected_root_entity=cand.entity_id,
                    affected_components=cand.explains,
                    supporting_evidence=symptoms_ev + topo_ev,
                    missing_evidence=[f"direct telemetry for {cand.entity_id}"],
                    validation_steps=self._validation_steps(
                        RootCauseCategory.UPSTREAM_DEPENDENCY if unmonitored else RootCauseCategory.UNKNOWN
                    ),
                    confidence_rationale="Only service-level symptoms and topology; the entity itself is unobserved.",
                    generated_by="heuristic",
                )
            )
        if not out:
            out.append(
                Hypothesis(
                    hypothesis_id="hyp-01",
                    description=(
                        "Service-level degradation without any localized anomaly; the cause cannot be "
                        "determined from available evidence."
                    ),
                    cause_category=RootCauseCategory.UNKNOWN,
                    suspected_root_entity=None,
                    affected_components=loc.symptoms,
                    supporting_evidence=symptoms_ev + topo_ev,
                    missing_evidence=["any entity-level anomaly that explains the service symptoms"],
                    validation_steps=self._validation_steps(RootCauseCategory.UNKNOWN),
                    confidence_rationale="No candidate entity shows an anomaly of its own.",
                    generated_by="heuristic",
                )
            )
        return out[:MAX_HYPOTHESES]
