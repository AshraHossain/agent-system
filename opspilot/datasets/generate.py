"""Deterministic synthetic dataset generator.

For each evaluation case, builds `<data_dir>/<case_id>.db` containing the
(possibly incomplete) topology, 8 hours of 5-minute telemetry with the case's
injected faults, and the knowledge corpus. The fault specification and labels
are NOT written to the database: the agents only ever see the observable world.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import numpy as np

from opspilot.core.dataset import SCHEMA, fmt_ts, parse_ts
from opspilot.core.topology import SVC, Topology, svc_name
from opspilot.datasets import spec

GENERATOR_VERSION = "1"
HISTORY_MIN = 480
INTERVAL_MIN = 5

SERVICE_BASE_LATENCY = {
    "checkout": 180.0,
    "payments": 220.0,
    "auth": 60.0,
    "search": 120.0,
    "catalog": 90.0,
    "video-stream": 140.0,
    "notifications": 250.0,
    "analytics": 300.0,
}
# Service latency multiplier when fully exposed to the fault type.
SERVICE_EFFECT = {
    "congestion": 2.2,
    "physical": 2.0,
    "saturation": 2.5,
    "dns": 1.8,
    "wan": 2.5,
}


def _metrics_for(comp: dict) -> dict[str, tuple[float, float]]:
    """metric -> (mean, sd) baseline per component type."""
    t = comp["type"]
    if t == "link":
        if comp["id"] == "lnk-c1-wan":
            return {
                "utilization_pct": (35, 3),
                "packet_loss_pct": (0.01, 0.005),
                "error_rate": (0.05, 0.03),
                "latency_ms": (12.0, 0.6),
                "probe_loss_pct": (0.01, 0.005),
            }
        return {
            "utilization_pct": (40, 4),
            "packet_loss_pct": (0.01, 0.005),
            "error_rate": (0.1, 0.05),
            "latency_ms": (0.5, 0.05),
            "probe_loss_pct": (0.01, 0.005),
        }
    if t in ("switch", "router", "wan_router"):
        return {"cpu_pct": (25, 3), "mem_pct": (45, 1)}
    if t in ("firewall", "load_balancer"):
        return {"cpu_pct": (35, 3), "mem_pct": (50, 1), "session_util_pct": (40, 3)}
    if t == "dns_server":
        return {"cpu_pct": (20, 3), "query_latency_ms": (3.0, 0.3)}
    if t == "service":
        base = SERVICE_BASE_LATENCY[svc_name(comp["id"])]
        return {"latency_p95_ms": (base, base * 0.05), "error_pct": (0.2, 0.05)}
    return {}


def _component_effect(
    ftype: str, metric: str, base: float, rng: np.random.Generator
) -> float | None:
    """Faulted value for the faulty component itself (None = unchanged)."""
    table = {
        "congestion": {
            "utilization_pct": lambda: rng.normal(97, 1.2),
            "latency_ms": lambda: base * rng.normal(6, 0.5),
            "packet_loss_pct": lambda: abs(rng.normal(0.6, 0.15)),
            "probe_loss_pct": lambda: abs(rng.normal(0.5, 0.15)),
        },
        "physical": {
            "error_rate": lambda: abs(rng.normal(60, 12)),
            "packet_loss_pct": lambda: abs(rng.normal(3.0, 0.6)),
            "probe_loss_pct": lambda: abs(rng.normal(2.8, 0.6)),
            "latency_ms": lambda: base * rng.normal(1.3, 0.05),
        },
        "saturation": {
            "cpu_pct": lambda: min(100.0, rng.normal(97, 1.2)),
            "session_util_pct": lambda: min(100.0, rng.normal(98, 0.8)),
            "mem_pct": lambda: base + 12,
        },
        "dns": {
            "query_latency_ms": lambda: abs(rng.normal(220, 35)),
            "cpu_pct": lambda: min(100.0, rng.normal(93, 2)),
        },
        "wan": {
            "latency_ms": lambda: abs(rng.normal(65, 7)),
            "packet_loss_pct": lambda: abs(rng.normal(2.0, 0.4)),
            "probe_loss_pct": lambda: abs(rng.normal(2.0, 0.4)),
        },
        "phantom_loss": {"packet_loss_pct": lambda: abs(rng.normal(2.0, 0.35))},
    }
    fn = table.get(ftype, {}).get(metric)
    return float(fn()) if fn else None


def _seed(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16)


def build_case(case_id: str, data_dir: Path) -> Path:
    case = spec.get_case(case_id)
    tspec = spec.topology_spec()
    world = case.get("world", {})
    faults = world.get("faults", [])
    true_topo = Topology.from_spec(tspec)
    doc_topo = Topology.from_spec(tspec, world.get("topology_overrides", {}).get("remove_edges"))
    rng = np.random.default_rng(_seed(case_id))
    reported = parse_ts(case["reported_at"])
    start = reported - timedelta(minutes=HISTORY_MIN)
    times = [
        start + timedelta(minutes=INTERVAL_MIN * i) for i in range(HISTORY_MIN // INTERVAL_MIN)
    ]
    offs = np.array([(t - reported).total_seconds() / 60 for t in times])

    rows: list[tuple[str, str, str, float]] = []
    for cid in sorted(true_topo.components):
        comp = true_topo.components[cid]
        for metric, (mean, sd) in _metrics_for(comp).items():
            values = rng.normal(mean, sd, len(times))
            if metric.endswith(("loss_pct", "error_rate", "error_pct")):
                values = np.abs(values)
            for f in faults:
                if f["type"] in ("gap", "delay"):
                    continue
                active = offs >= f["start_min"]
                if f["entity"] == cid:
                    for i in np.nonzero(active)[0]:
                        v = _component_effect(f["type"], metric, values[i], rng)
                        if v is not None:
                            values[i] = v
                elif comp["type"] == "service" and f["type"] in SERVICE_EFFECT:
                    share = true_topo.traffic_share(cid, f["entity"])
                    if share > 0:
                        if metric == "latency_p95_ms":
                            values[active] *= 1 + (SERVICE_EFFECT[f["type"]] - 1) * share
                        elif metric == "error_pct" and f["type"] in ("physical", "wan"):
                            values[active] += 2.0 * share
            keep = np.ones(len(times), dtype=bool)
            for f in faults:
                if f["entity"] != cid:
                    continue
                if f["type"] == "gap" and metric in f.get("metrics", [metric]):
                    keep &= offs < f["start_min"]
                if f["type"] == "delay":
                    keep &= offs <= -f["lag_min"]
            rows.extend(
                (fmt_ts(times[i]), cid, metric, float(round(values[i], 4)))
                for i in range(len(times))
                if keep[i]
            )

    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{case_id}.db"
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("dataset_id", case_id),
                ("reported_at", case["reported_at"]),
                ("sample_interval_min", str(INTERVAL_MIN)),
                ("generator_version", GENERATOR_VERSION),
                ("site", tspec["site"]),
            ],
        )
        con.executemany(
            "INSERT INTO components VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    c["id"],
                    c["type"],
                    c["redundancy_group"],
                    c["tier"],
                    json.dumps(c["interfaces"]),
                    json.dumps(c["endpoints"]),
                )
                for c in doc_topo.components.values()
            ],
        )
        con.executemany(
            "INSERT INTO edges VALUES (?, ?, ?, ?)",
            [(e.src, e.dst, e.group, e.confidence) for e in doc_topo.edges],
        )
        con.executemany("INSERT INTO telemetry VALUES (?, ?, ?, ?)", rows)
        con.executemany(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    d["doc_id"],
                    d["doc_type"],
                    d["title"],
                    d["status"],
                    d["updated"],
                    d.get("superseded_by"),
                    d.get("supersedes"),
                    json.dumps(d.get("components", [])),
                    json.dumps(d.get("tags", [])),
                    d.get("root_cause_category"),
                    d["body"],
                )
                for d in spec.corpus(world.get("extra_documents"))
            ],
        )
        con.commit()
    finally:
        con.close()
    tmp.replace(path)
    return path


def build_all(data_dir: Path, case_ids: list[str] | None = None) -> list[Path]:
    return [build_case(c, data_dir) for c in (case_ids or spec.case_ids())]


__all__ = ["SVC", "build_all", "build_case"]
