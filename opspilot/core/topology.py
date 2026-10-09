"""Dependency graph analysis over the synthetic topology.

Graph model: directed edges point from a dependent to a requirement. Edges that
share a `group` from the same source are redundant alternatives (any one
suffices); ungrouped edges are hard requirements. Services are nodes named
`svc:<name>`.

All traversals are cycle-safe and depth-bounded.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.topology import (
    BlastRadius,
    ComponentInfo,
    DependencyResult,
    ServiceImpact,
)
from opspilot.core.errors import InvalidArgument, NotFound

MAX_DEPTH = 12
SVC = "svc:"

# Explicit blast-radius rule table: (exposure, tier) -> impact.
IMPACT_RULES: dict[tuple[str, int], str] = {
    ("single_point", 1): "critical",
    ("single_point", 2): "high",
    ("single_point", 3): "medium",
    ("redundant", 1): "high",
    ("redundant", 2): "medium",
    ("redundant", 3): "low",
    ("indirect", 1): "medium",
    ("indirect", 2): "low",
    ("indirect", 3): "low",
}
IMPACT_ORDER = {"none": 0, "unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    group: str | None
    confidence: str = "documented"


def svc_node(service: str) -> str:
    return service if service.startswith(SVC) else f"{SVC}{service}"


def svc_name(node: str) -> str:
    return node[len(SVC):] if node.startswith(SVC) else node


class Topology:
    def __init__(self, components: dict[str, dict], edges: list[Edge]):
        self.components = components
        self.edges = edges
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        for e in edges:
            self._out[e.src].append(e)
            self._in[e.dst].append(e)

    # ---------- construction ----------
    @classmethod
    def from_dataset(cls, ds) -> Topology:
        return cls(ds.components, [Edge(s, d, g, c) for s, d, g, c in ds.edges])

    @classmethod
    def from_spec(cls, spec: dict, remove_edges: list[dict] | None = None) -> Topology:
        comps: dict[str, dict] = {}
        for c in spec["components"]:
            comps[c["id"]] = {
                "id": c["id"], "type": c["type"], "redundancy_group": c.get("redundancy_group"),
                "tier": None, "interfaces": [], "endpoints": [],
            }
        for link in spec["links"]:
            comps[link["id"]] = {
                "id": link["id"], "type": "link", "redundancy_group": None, "tier": None,
                "interfaces": [link["a"], link["b"]],
                "endpoints": [link["a"].split(":")[0], link["b"].split(":")[0]],
                "capacity_gbps": link.get("capacity_gbps"),
            }
            for iface in (link["a"], link["b"]):
                dev = iface.split(":")[0]
                comps[dev]["interfaces"].append(iface)
        for s in spec["services"]:
            comps[svc_node(s["id"])] = {
                "id": svc_node(s["id"]), "type": "service", "redundancy_group": None,
                "tier": s["tier"], "interfaces": [], "endpoints": [],
            }
        removed = {(r["src"], r["dst"]) for r in (remove_edges or [])}
        edges = [
            Edge(e["src"], e["dst"], e.get("group"), e.get("confidence", "documented"))
            for e in spec["edges"]
            if (e["src"], e["dst"]) not in removed
        ]
        return cls(comps, edges)

    # ---------- basic lookups ----------
    @property
    def services(self) -> list[str]:
        return sorted(svc_name(c) for c, v in self.components.items() if v["type"] == "service")

    def is_known(self, node: str) -> bool:
        return node in self.components

    def tier(self, service: str) -> int:
        return int(self.components[svc_node(service)]["tier"] or 3)

    def lookup(self, component_id: str) -> ComponentInfo:
        node = component_id
        if node not in self.components and svc_node(node) in self.components:
            node = svc_node(node)
        if node not in self.components:
            ev = Evidence.make(
                EvidenceKind.TOPOLOGY,
                f"{component_id} is not present in the topology inventory",
                f"topology:component/{component_id}",
                entity_id=component_id,
                data={"known": False},
            )
            return ComponentInfo(component_id=component_id, type="unknown", known=False,
                                 evidence=[ev])
        c = self.components[node]
        ev = Evidence.make(
            EvidenceKind.TOPOLOGY,
            f"{node} is a {c['type']}"
            + (f" in redundancy group {c['redundancy_group']}" if c["redundancy_group"] else "")
            + (f" connecting {' and '.join(c['interfaces'])}" if c["type"] == "link" else ""),
            f"topology:component/{node}",
            entity_id=node,
            data={"type": c["type"], "redundancy_group": c["redundancy_group"]},
        )
        return ComponentInfo(
            component_id=node, type=c["type"], redundancy_group=c["redundancy_group"],
            interfaces=c["interfaces"], endpoints=c["endpoints"], tier=c["tier"], evidence=[ev],
        )

    # ---------- traversal ----------
    def closure(self, node: str, *, through_services: bool = True) -> set[str]:
        """All nodes reachable from `node` (excluding itself), depth-bounded BFS."""
        seen: set[str] = set()
        queue = deque([(node, 0)])
        while queue:
            cur, depth = queue.popleft()
            if depth >= MAX_DEPTH:
                continue
            for e in self._out.get(cur, []):
                if e.dst in seen or e.dst == node:
                    continue
                if not through_services and e.dst.startswith(SVC):
                    continue
                seen.add(e.dst)
                queue.append((e.dst, depth + 1))
        return seen

    def dependents(self, node: str) -> set[str]:
        """All nodes that (transitively) depend on `node`."""
        seen: set[str] = set()
        queue = deque([(node, 0)])
        while queue:
            cur, depth = queue.popleft()
            if depth >= MAX_DEPTH:
                continue
            for e in self._in.get(cur, []):
                if e.src not in seen and e.src != node:
                    seen.add(e.src)
                    queue.append((e.src, depth + 1))
        return seen

    def satisfied(self, node: str, failed: set[str], _stack: frozenset = frozenset()) -> bool:
        """Is `node` operational if every component in `failed` is down?"""
        if node in failed:
            return False
        if node in _stack:
            return True  # cycle: do not count a node as its own requirement
        stack = _stack | {node}
        groups: dict[str, list[Edge]] = defaultdict(list)
        for e in self._out.get(node, []):
            groups[e.group or f"__hard__{e.dst}"].append(e)
        return all(any(self.satisfied(e.dst, failed, stack) for e in alts)
                   for alts in groups.values())

    def exposure(self, service: str, component: str) -> Literal[
        "single_point", "redundant", "indirect"
    ] | None:
        node = svc_node(service)
        direct = self.closure(node, through_services=False)
        if component in direct:
            return "redundant" if self.satisfied(node, {component}) else "single_point"
        if component in self.closure(node):
            return "indirect"
        return None

    def paths(self, start: str, target: str, limit: int = 3) -> list[list[str]]:
        """Up to `limit` simple paths start -> target (BFS order, depth-bounded)."""
        out: list[list[str]] = []
        queue = deque([[start]])
        while queue and len(out) < limit:
            path = queue.popleft()
            if len(path) > MAX_DEPTH:
                continue
            for e in self._out.get(path[-1], []):
                if e.dst in path:
                    continue
                if e.dst == target:
                    out.append([*path, e.dst])
                else:
                    queue.append([*path, e.dst])
        return out

    # ---------- analyses ----------
    def dependencies(self, services: list[str]) -> DependencyResult:
        if not services:
            raise InvalidArgument("at least one service is required")
        known, unknown = [], []
        for s in services:
            (known if svc_node(s) in self.components else unknown).append(svc_name(s))
        deps: dict[str, list[str]] = {}
        evidence: list[Evidence] = []
        uncertainties: list[str] = []
        for s in known:
            comps = sorted(c for c in self.closure(svc_node(s)) if not c.startswith(SVC))
            deps[s] = comps
            inferred = [e for e in self._out.get(svc_node(s), []) if e.confidence != "documented"]
            for e in inferred:
                uncertainties.append(
                    f"dependency {s} -> {svc_name(e.dst)} is {e.confidence}, not documented"
                )
            evidence.append(Evidence.make(
                EvidenceKind.TOPOLOGY,
                f"{s} depends on {len(comps)} network components",
                f"topology:dependencies/{s}",
                entity_id=svc_node(s),
                data={"component_count": len(comps), "tier": self.tier(s)},
            ))
        shared: dict[str, list[str]] = defaultdict(list)
        for s, comps in deps.items():
            for c in comps:
                shared[c].append(s)
        shared_multi = {c: sorted(v) for c, v in sorted(shared.items()) if len(v) >= 2}
        for c, svcs in shared_multi.items():
            evidence.append(Evidence.make(
                EvidenceKind.TOPOLOGY,
                f"{c} is a shared dependency of {', '.join(svcs)}",
                f"topology:shared/{c}",
                entity_id=c,
                data={"service_count": len(svcs), "services": ",".join(svcs)},
            ))
        for u in unknown:
            uncertainties.append(f"service {u} is not in the topology inventory")
        return DependencyResult(
            services=known, dependencies=deps, shared_components=shared_multi,
            unknown_services=unknown, uncertainties=uncertainties, evidence=evidence,
        )

    def blast_radius(self, component_id: str) -> BlastRadius:
        if component_id not in self.components:
            raise NotFound(f"component {component_id} not in topology")
        impacts: list[ServiceImpact] = []
        for node in sorted(self.dependents(component_id)):
            if not node.startswith(SVC):
                continue
            s = svc_name(node)
            exp = self.exposure(s, component_id)
            if exp is None:
                continue
            tier = self.tier(s)
            impacts.append(ServiceImpact(service=s, tier=tier, exposure=exp,
                                         impact=IMPACT_RULES[(exp, tier)]))
        impacts.sort(key=lambda i: (-IMPACT_ORDER[i.impact], i.service))
        comp = self.components[component_id]
        uncertainties = []
        if comp["redundancy_group"] or any(e.group for e in self._in.get(component_id, [])):
            uncertainties.append(
                "redundancy assumes the alternative path is healthy and has spare capacity"
            )
        inferred = [e for e in self.edges if e.confidence != "documented"]
        if any(e.src in {svc_node(i.service) for i in impacts} for e in inferred):
            uncertainties.append("some dependencies used here are inferred, not documented")
        rules = [
            "exposure=single_point if the service fails when the component fails",
            "exposure=redundant if an alternative path keeps the service up",
            "exposure=indirect if reached only through another service",
            "impact from (exposure, tier) table: single_point/1=critical ... indirect/3=low",
        ]
        max_imp = impacts[0].impact if impacts else "none"
        ev = Evidence.make(
            EvidenceKind.TOPOLOGY,
            f"Potential blast radius of {component_id}: {len(impacts)} services, max impact "
            f"{max_imp}",
            f"topology:blast_radius/{component_id}",
            entity_id=component_id,
            data={"services": ",".join(i.service for i in impacts), "max_impact": max_imp},
        )
        return BlastRadius(component_id=component_id, impacts=impacts, rules=rules,
                           uncertainties=uncertainties, evidence=[ev])

    def traffic_share(self, node: str, target: str, _stack: frozenset = frozenset()) -> float:
        """Approximate share of `node`'s traffic that crosses `target`.

        Used only by the synthetic data generator to scale injected effects.
        Alternatives in a group split traffic evenly (ECMP-like); service-to-
        service hops are damped by 0.6.
        """
        if node == target:
            return 1.0
        if node in _stack:
            return 0.0
        stack = _stack | {node}
        groups: dict[str, list[Edge]] = defaultdict(list)
        for e in self._out.get(node, []):
            groups[e.group or f"__hard__{e.dst}"].append(e)
        best = 0.0
        for alts in groups.values():
            share = sum(self.traffic_share(e.dst, target, stack) for e in alts) / len(alts)
            if node.startswith(SVC) and any(e.dst.startswith(SVC) for e in alts):
                share *= 0.6
            best = max(best, share)
        return best
