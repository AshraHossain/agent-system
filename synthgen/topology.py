"""Synthetic network topology: 18 monitored nodes, 1 unmonitored upstream, 25 links, 5 services.

Deterministic (no randomness): the same topology underlies every scenario so
that topology reasoning is consistent across cases.
"""

from __future__ import annotations

from typing import Any

# (node_id, role, tier, monitored)
NODES: list[tuple[str, str, str, bool]] = [
    ("isp-transit", "upstream_provider", "external", False),
    ("pe-1", "provider_edge_router", "edge", True),
    ("fw-1", "firewall", "edge", True),
    ("fw-2", "firewall", "edge", True),
    ("core-1", "core_router", "core", True),
    ("core-2", "core_router", "core", True),
    ("dns-1", "dns_server", "core", True),
    *[(f"agg-{i}", "aggregation_switch", "aggregation", True) for i in range(1, 5)],
    *[(f"acc-{i}", "access_switch", "access", True) for i in range(1, 9)],
]

# (a, b, capacity_gbps)
_LINK_PAIRS: list[tuple[str, str, int]] = [
    ("pe-1", "isp-transit", 10),
    ("pe-1", "fw-1", 10),
    ("pe-1", "fw-2", 10),
    ("fw-1", "core-1", 10),
    ("fw-2", "core-2", 10),
    ("core-1", "core-2", 40),
    ("core-1", "dns-1", 1),
    ("core-1", "agg-1", 40),
    ("core-1", "agg-2", 40),
    ("core-2", "agg-1", 40),
    ("core-2", "agg-2", 40),
    ("core-1", "agg-3", 40),
    ("core-1", "agg-4", 40),
    ("core-2", "agg-3", 40),
    ("core-2", "agg-4", 40),
    ("agg-1", "agg-2", 10),
    ("agg-3", "agg-4", 10),
    ("agg-1", "acc-1", 10),
    ("agg-1", "acc-2", 10),
    ("agg-2", "acc-3", 10),
    ("agg-2", "acc-4", 10),
    ("agg-3", "acc-5", 10),
    ("agg-3", "acc-6", 10),
    ("agg-4", "acc-7", 10),
    ("agg-4", "acc-8", 10),
]


def link_id(a: str, b: str) -> str:
    return f"link-{a}-{b}"


LINKS: list[dict[str, Any]] = [
    {"link_id": link_id(a, b), "a": a, "b": b, "capacity_gbps": cap} for a, b, cap in _LINK_PAIRS
]

# Services: the nodes they depend on, and a representative probe path (links)
# whose latency/loss the synthetic service probe aggregates.
SERVICES: list[dict[str, Any]] = [
    {
        "service_id": "voip",
        "criticality": "high",
        "customers": 4200,
        "depends_on": ["acc-1", "acc-2", "acc-3", "acc-4", "agg-1", "agg-2", "core-1", "core-2", "fw-1", "pe-1"],
        "probe_path": [
            link_id("agg-1", "acc-1"),
            link_id("core-1", "agg-1"),
            link_id("fw-1", "core-1"),
            link_id("pe-1", "fw-1"),
        ],
        "base_latency_ms": 18.0,
    },
    {
        "service_id": "enterprise_vpn",
        "criticality": "high",
        "customers": 310,
        "depends_on": ["acc-3", "acc-4", "agg-2", "core-1", "fw-1", "pe-1"],
        "probe_path": [
            link_id("agg-2", "acc-3"),
            link_id("core-1", "agg-2"),
            link_id("fw-1", "core-1"),
            link_id("pe-1", "fw-1"),
        ],
        "base_latency_ms": 26.0,
    },
    {
        "service_id": "video",
        "criticality": "medium",
        "customers": 9800,
        "depends_on": ["acc-5", "acc-6", "acc-7", "acc-8", "agg-3", "agg-4", "core-2", "fw-2", "pe-1"],
        "probe_path": [
            link_id("agg-3", "acc-6"),
            link_id("core-2", "agg-3"),
            link_id("fw-2", "core-2"),
            link_id("pe-1", "fw-2"),
        ],
        "base_latency_ms": 24.0,
    },
    {
        "service_id": "internet",
        "criticality": "high",
        "customers": 15500,
        "depends_on": ["acc-7", "agg-4", "core-2", "fw-2", "pe-1", "isp-transit"],
        "probe_path": [
            link_id("agg-4", "acc-7"),
            link_id("core-2", "agg-4"),
            link_id("fw-2", "core-2"),
            link_id("pe-1", "fw-2"),
            link_id("pe-1", "isp-transit"),
        ],
        "base_latency_ms": 32.0,
    },
    {
        "service_id": "dns",
        "criticality": "high",
        "customers": 15500,
        "depends_on": ["dns-1", "core-1"],
        "probe_path": [link_id("core-1", "dns-1")],
        "base_latency_ms": 4.0,
    },
]


def monitored_nodes() -> list[str]:
    return [n for n, _, _, monitored in NODES if monitored]


def node_tier(node_id: str) -> str:
    return next(t for n, _, t, _ in NODES if n == node_id)


def build_topology() -> dict[str, Any]:
    return {
        "provenance": "synthetic",
        "nodes": [
            {"node_id": n, "role": role, "tier": tier, "monitored": monitored} for n, role, tier, monitored in NODES
        ],
        "links": LINKS,
        "services": [{k: v for k, v in s.items() if k != "base_latency_ms"} for s in SERVICES],
    }
