"""Deterministic topology tools: traversal, blast radius, and symptom localization.

Localization ranks entities by how many observed symptoms their failure
*could* explain, given the dependency structure. That is a correlation
with structure, not proof of causation. The output says so, and it flags
when no candidate has a symptom of its own.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from netpulse.data.schemas import EventRecord, MaintenanceWindow
from netpulse.errors import ToolInputError
from netpulse.models import EvidenceItem, EvidenceSource
from netpulse.topology.model import Topology

_CRITICALITY_RANK = {"low": 0, "medium": 1, "high": 2}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# Tool schemas
# --------------------------------------------------------------------------


class PathResult(_Model):
    source: str
    target: str
    nodes: list[str]
    links: list[str]


class BlastRadius(_Model):
    entity_id: str
    entity_kind: Literal["node", "link"]
    isolated_nodes: list[str]  # lose every path to the upstream provider
    affected_services: list[str]  # depend on, or are probed through, the entity
    customers_at_risk: int
    max_service_criticality: Literal["none", "low", "medium", "high"]
    redundant: bool  # True when the failure isolates nothing


class LocalizationQuery(_Model):
    symptomatic_entities: list[str] = Field(min_length=1, max_length=200)
    max_candidates: int = Field(default=8, ge=1, le=50)


class Candidate(_Model):
    entity_id: str
    entity_kind: Literal["node", "link"]
    explains: list[str]
    has_own_symptom: bool
    monitored: bool
    blast_radius_size: int


class LocalizationResult(_Model):
    symptoms: list[str]
    candidates: list[Candidate]
    minimal_cover: list[str]  # greedy set of symptomatic candidates that explains all it can
    unexplained_by_cover: list[str]
    corroborated: bool  # at least one candidate shows a symptom of its own
    caveat: str = (
        "Candidates are ranked by structural ability to explain symptoms. "
        "This is correlation with topology, not proof of causation."
    )


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


class TopologyGraph:
    def __init__(self, topology: Topology) -> None:
        self.topology = topology
        self.nodes = {n.node_id: n for n in topology.nodes}
        self.links = {ln.link_id: ln for ln in topology.links}
        self.services = {s.service_id: s for s in topology.services}
        self.adjacency: dict[str, list[tuple[str, str]]] = {n: [] for n in self.nodes}
        for link in topology.links:
            self.adjacency[link.a].append((link.b, link.link_id))
            self.adjacency[link.b].append((link.a, link.link_id))
        for neighbours in self.adjacency.values():
            neighbours.sort()
        self.roots = sorted(n.node_id for n in topology.nodes if n.tier == "external")

    # ------------------------------------------------------------- helpers

    def kind(self, entity_id: str) -> Literal["node", "link", "service"]:
        kind = self.topology.kind(entity_id)
        if kind is None:
            raise ToolInputError(f"unknown entity {entity_id!r}")
        return kind

    def endpoints(self, link_id: str) -> tuple[str, str]:
        link = self.links[link_id]
        return link.a, link.b

    def is_monitored(self, entity_id: str) -> bool:
        kind = self.kind(entity_id)
        if kind == "node":
            return self.nodes[entity_id].monitored
        return True  # links are measured at a monitored endpoint; services by probes

    def _reachable(self, removed_node: str | None = None, removed_link: str | None = None) -> set[str]:
        seen = {r for r in self.roots if r != removed_node}
        queue = deque(seen)
        while queue:
            current = queue.popleft()
            for neighbour, link_id in self.adjacency[current]:
                if neighbour == removed_node or link_id == removed_link or neighbour in seen:
                    continue
                seen.add(neighbour)
                queue.append(neighbour)
        return seen

    # --------------------------------------------------------------- tools

    def shortest_path(self, source: str, target: str) -> PathResult:
        for entity in (source, target):
            if self.kind(entity) != "node":
                raise ToolInputError(f"{entity!r} is not a node")
        previous: dict[str, tuple[str, str] | None] = {source: None}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            if current == target:
                break
            for neighbour, link_id in self.adjacency[current]:
                if neighbour not in previous:
                    previous[neighbour] = (current, link_id)
                    queue.append(neighbour)
        if target not in previous:
            raise ToolInputError(f"no path between {source!r} and {target!r}")
        nodes, links = [target], []
        while previous[nodes[-1]] is not None:
            prev, link_id = previous[nodes[-1]]
            links.append(link_id)
            nodes.append(prev)
        return PathResult(source=source, target=target, nodes=nodes[::-1], links=links[::-1])

    def blast_radius(self, entity_id: str) -> BlastRadius:
        kind = self.kind(entity_id)
        if kind == "service":
            raise ToolInputError("blast radius is defined for nodes and links, not services")
        if kind == "node":
            reachable = self._reachable(removed_node=entity_id)
            isolated = sorted(set(self.nodes) - reachable - {entity_id} - set(self.roots))
            services = [
                s for s in self.services.values() if entity_id in s.depends_on or set(isolated) & set(s.depends_on)
            ]
        else:
            reachable = self._reachable(removed_link=entity_id)
            isolated = sorted(set(self.nodes) - reachable - set(self.roots))
            a, b = self.endpoints(entity_id)
            services = [
                s
                for s in self.services.values()
                if entity_id in s.probe_path or {a, b} <= set(s.depends_on) or set(isolated) & set(s.depends_on)
            ]
        criticality = max((s.criticality for s in services), key=_CRITICALITY_RANK.__getitem__, default="none")
        return BlastRadius(
            entity_id=entity_id,
            entity_kind=kind,
            isolated_nodes=isolated,
            affected_services=sorted(s.service_id for s in services),
            customers_at_risk=sum(s.customers for s in services),
            max_service_criticality=criticality,
            redundant=not isolated,
        )

    def explains(self, candidate: str, symptom: str) -> bool:
        """Could a fault at ``candidate`` plausibly produce a symptom observed on ``symptom``?"""
        if candidate == symptom:
            return True
        c_kind, s_kind = self.kind(candidate), self.kind(symptom)
        if s_kind == "service":
            svc = self.services[symptom]
            return candidate in svc.depends_on or candidate in svc.probe_path
        if s_kind == "link" and c_kind == "node":
            return candidate in self.endpoints(symptom)
        return False

    def localize(self, query: LocalizationQuery) -> LocalizationResult:
        symptoms = sorted(set(query.symptomatic_entities))
        for s in symptoms:
            self.kind(s)
        candidates = []
        for entity in [*sorted(self.nodes), *sorted(self.links)]:
            explained = [s for s in symptoms if self.explains(entity, s)]
            if not explained:
                continue
            candidates.append(
                Candidate(
                    entity_id=entity,
                    entity_kind=self.kind(entity),
                    explains=explained,
                    has_own_symptom=entity in symptoms,
                    monitored=self.is_monitored(entity),
                    blast_radius_size=len(self.blast_radius(entity).affected_services),
                )
            )
        # Own symptom first, then breadth of explanation, then the most specific (smallest blast radius).
        candidates.sort(key=lambda c: (not c.has_own_symptom, -len(c.explains), c.blast_radius_size, c.entity_id))

        cover, remaining = [], set(symptoms)
        for cand in (c for c in candidates if c.has_own_symptom):
            gain = remaining & set(cand.explains)
            if gain:
                cover.append(cand.entity_id)
                remaining -= gain
        return LocalizationResult(
            symptoms=symptoms,
            candidates=candidates[: query.max_candidates],
            minimal_cover=cover,
            unexplained_by_cover=sorted(remaining),
            corroborated=any(c.has_own_symptom for c in candidates),
        )

    # ----------------------------------------------------- change context

    def expand(self, entities: list[str]) -> set[str]:
        """Entities plus link endpoints, so a link symptom also matches events on its routers."""
        out = set(entities)
        for e in entities:
            if self.topology.kind(e) == "link":
                out.update(self.endpoints(e))
        return out

    def related_events(
        self,
        events: list[EventRecord],
        entities: list[str],
        start: datetime,
        end: datetime,
        lookback: timedelta = timedelta(hours=2),
    ) -> list[EventRecord]:
        scope = self.expand(entities)
        return [e for e in events if e.entity_id in scope and start - lookback <= e.timestamp <= end]

    def overlapping_maintenance(
        self, windows: list[MaintenanceWindow], entities: list[str], start: datetime, end: datetime
    ) -> list[MaintenanceWindow]:
        scope = self.expand(entities)
        hits = []
        for w in windows:
            touches = w.entity_id in scope or bool(self.expand([w.entity_id]) & scope)
            if w.start <= end and start <= w.end and touches:
                hits.append(w)
        return hits


# --------------------------------------------------------------------------
# Evidence rendering
# --------------------------------------------------------------------------


def blast_radius_evidence(br: BlastRadius, evidence_id: str) -> EvidenceItem:
    isolated = ", ".join(br.isolated_nodes) or "none (redundant paths exist)"
    services = ", ".join(br.affected_services) or "none"
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.TOPOLOGY,
        summary=(
            f"Blast radius of {br.entity_kind} {br.entity_id}: isolated nodes: {isolated}; "
            f"services depending on it: {services}; customers at risk: {br.customers_at_risk}; "
            f"highest service criticality: {br.max_service_criticality}"
        )[:600],
        entity_ids=[br.entity_id, *br.isolated_nodes, *br.affected_services],
        method="topology.blast_radius",
    )


def localization_evidence(result: LocalizationResult, evidence_id: str, top: int = 3) -> EvidenceItem:
    """Verdict first, so truncation can only shorten the candidate list, never drop the conclusion."""
    if result.corroborated:
        verdict = (
            f"Topology localization (structural correlation, not proof of cause); minimal cover: "
            f"{', '.join(result.minimal_cover)}."
        )
    else:
        verdict = "Topology localization: NO candidate shows an anomaly of its own (structural correlation only)."
    unmonitored = [c.entity_id for c in result.candidates if not c.monitored]
    if unmonitored:
        verdict += f" Unmonitored candidates (no telemetry, cannot be verified): {', '.join(unmonitored)}."
    parts = [
        f"{c.entity_id} explains {len(c.explains)}/{len(result.symptoms)}"
        + (" (own anomaly)" if c.has_own_symptom else "")
        for c in result.candidates[:top]
    ]
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.TOPOLOGY,
        summary=(verdict + " Candidates: " + "; ".join(parts))[:600],
        entity_ids=[c.entity_id for c in result.candidates],  # all candidates, so any can be cited as grounded
        method="topology.localize",
    )


def event_evidence(event: EventRecord, evidence_id: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.EVENT_LOG,
        summary=f"{event.timestamp:%Y-%m-%d %H:%M} UTC {event.severity} {event.event_type} on {event.entity_id}: "
        f"{event.message}"[:600],
        entity_ids=[event.entity_id],
        window_start=event.timestamp,
        window_end=event.timestamp,
        method="event_log",
    )


def maintenance_evidence(window: MaintenanceWindow, evidence_id: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.MAINTENANCE,
        summary=f"Planned maintenance {window.ticket} on {window.entity_id} "
        f"{window.start:%Y-%m-%d %H:%M}–{window.end:%H:%M} UTC: {window.description}"[:600],
        entity_ids=[window.entity_id],
        window_start=window.start,
        window_end=window.end,
        source_ref=f"maintenance:{window.ticket}",
        method="maintenance_calendar",
    )
