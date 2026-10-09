"""Synthetic telemetry rendering with counterfactual ground truth.

Every scenario is rendered twice from the *same* noise draw: once without
faults (counterfactual) and once with them. A sample is labelled anomalous
when the faulted value departs materially from its counterfactual. Ground
truth therefore reflects what the injected faults actually did to the data
(including knock-on effects on latency, loss and service probes), not what a
hand-written label assumes they did. Noise alone is never labelled anomalous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from synthgen.topology import LINKS, SERVICES, monitored_nodes, node_tier

SAMPLE_MINUTES = 5
N_SAMPLES = 360  # 30 hours: 24 h of history + 6 h containing the incident

LINK_METRICS = ("utilization_pct", "latency_ms", "packet_loss_pct", "error_rate")
NODE_METRICS = ("cpu_pct", "memory_pct")
SERVICE_METRICS = ("service_latency_ms", "service_success_pct")

Op = Literal["add", "pin", "set", "scale"]


@dataclass(frozen=True)
class Fault:
    """A change applied to one metric of one entity over [start, end) samples."""

    entity: str
    metric: str
    op: Op
    magnitude: float
    start: int
    end: int = N_SAMPLES
    ramp: int = 2  # samples to reach full effect

    def envelope(self) -> np.ndarray:
        env = np.zeros(N_SAMPLES)
        idx = np.arange(self.start, min(self.end, N_SAMPLES))
        if self.ramp > 0:
            env[idx] = np.minimum(1.0, (idx - self.start + 1) / self.ramp)
        else:
            env[idx] = 1.0
        return env


@dataclass(frozen=True)
class DataIssue:
    """Telemetry delivery problems: rows dropped (gap) or ingested late (delay)."""

    kind: Literal["gap", "delay"]
    entities: tuple[str, ...]
    start: int
    end: int = N_SAMPLES
    delay_s: int = 0


@dataclass
class NoiseProfile:
    multiplier: float = 1.0
    spike_prob: float = 0.0


@dataclass
class Rendered:
    links: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    nodes: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    services: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)

    def series(self, entity: str, metric: str) -> np.ndarray:
        for table in (self.links, self.nodes, self.services):
            if entity in table:
                return table[entity][metric]
        raise KeyError(entity)


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


def _link_profile(a: str, b: str) -> tuple[float, float]:
    """(base utilization %, base latency ms) by link role."""
    tiers = {node_tier(a), node_tier(b)}
    if "external" in tiers:
        return 45.0, 9.0
    if "edge" in tiers:
        return 40.0, 1.5
    if a.startswith("core") and b.startswith("core"):
        return 30.0, 0.6
    if "dns-1" in (a, b):
        return 12.0, 0.3
    if a.startswith("agg") and b.startswith("agg"):
        return 8.0, 0.4
    if a.startswith("core"):
        return 35.0, 0.6
    return 25.0, 0.4


def _node_cpu_base(node: str) -> float:
    if node.startswith("core"):
        return 35.0
    if node.startswith("fw"):
        return 40.0
    if node.startswith("pe"):
        return 30.0
    if node.startswith("agg"):
        return 25.0
    if node.startswith("dns"):
        return 20.0
    return 15.0


def _diurnal(start: datetime) -> np.ndarray:
    stamps = [start + timedelta(minutes=SAMPLE_MINUTES * i) for i in range(N_SAMPLES)]
    hours = np.array([t.hour + t.minute / 60 for t in stamps])
    return np.sin(2 * np.pi * (hours - 8.0) / 24.0)  # peak at 14:00 UTC


@dataclass
class Noise:
    """One noise draw shared by the counterfactual and faulted renders."""

    link: dict[str, dict[str, np.ndarray]]
    node: dict[str, dict[str, np.ndarray]]
    service: dict[str, dict[str, np.ndarray]]
    memory_base: dict[str, float]


def draw_noise(rng: np.random.Generator, profile: NoiseProfile) -> Noise:
    m = profile.multiplier

    def spikes(scale: float) -> np.ndarray:
        if profile.spike_prob <= 0:
            return np.zeros(N_SAMPLES)
        mask = rng.random(N_SAMPLES) < profile.spike_prob
        return mask * rng.uniform(0.5, 1.0, N_SAMPLES) * scale

    link = {}
    for link_def in LINKS:
        lid = link_def["link_id"]
        base_util, base_lat = _link_profile(link_def["a"], link_def["b"])
        link[lid] = {
            "utilization_pct": rng.normal(0, 2.0 * m, N_SAMPLES) + spikes(8.0),
            "latency_ms": rng.normal(0, 0.05 * base_lat * m, N_SAMPLES) + spikes(0.6 * base_lat),
            "packet_loss_pct": np.abs(rng.normal(0, 0.004 * m, N_SAMPLES)) + spikes(0.15),
            "error_rate": rng.poisson(0.2 * m, N_SAMPLES).astype(float) + spikes(2.0),
        }
    node = {}
    memory_base = {}
    for n in monitored_nodes():
        node[n] = {
            "cpu_pct": rng.normal(0, 2.0 * m, N_SAMPLES) + spikes(10.0),
            "memory_pct": rng.normal(0, 0.4 * m, N_SAMPLES),
        }
        memory_base[n] = float(rng.uniform(42, 60))
    service = {}
    for s in SERVICES:
        service[s["service_id"]] = {
            "service_latency_ms": rng.normal(0, 0.03 * s["base_latency_ms"] * m, N_SAMPLES)
            + spikes(0.2 * s["base_latency_ms"]),
            "service_success_pct": -np.abs(rng.normal(0, 0.02 * m, N_SAMPLES)),
        }
    return Noise(link=link, node=node, service=service, memory_base=memory_base)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _apply(values: np.ndarray, faults: list[Fault], entity: str, metric: str) -> np.ndarray:
    out = values.copy()
    for f in faults:
        if f.entity != entity or f.metric != metric:
            continue
        env = f.envelope()
        if f.op == "add":
            out = out + env * f.magnitude
        elif f.op == "pin":
            out = (1 - env) * out + env * (f.magnitude + (out - out.mean()) * 0.3)
        elif f.op == "scale":
            out = out * (1 + env * (f.magnitude - 1))
        elif f.op == "set":
            out = np.where(env > 0, f.magnitude, out)
    return out


def render(start: datetime, noise: Noise, faults: list[Fault]) -> Rendered:
    diurnal = _diurnal(start)
    idx = np.arange(N_SAMPLES)
    set_faults = [f for f in faults if f.op == "set"]
    shaping = [f for f in faults if f.op != "set"]
    out = Rendered()

    for link_def in LINKS:
        lid = link_def["link_id"]
        base_util, base_lat = _link_profile(link_def["a"], link_def["b"])
        n = noise.link[lid]
        util = base_util + 0.35 * base_util * diurnal + n["utilization_pct"]
        util = np.clip(_apply(util, shaping, lid, "utilization_pct"), 0, 100)
        # Queueing: latency and loss follow utilization, so congestion propagates.
        lat = base_lat + 0.004 * np.maximum(0, util - 65) ** 2 + n["latency_ms"]
        loss = 0.005 + 0.25 * np.maximum(0, util - 92) + n["packet_loss_pct"]
        errors = n["error_rate"].copy()
        lat = np.maximum(0.05, _apply(lat, shaping, lid, "latency_ms"))
        loss = np.clip(_apply(loss, shaping, lid, "packet_loss_pct"), 0, 100)
        errors = np.maximum(0, _apply(errors, shaping, lid, "error_rate"))
        out.links[lid] = {"utilization_pct": util, "latency_ms": lat, "packet_loss_pct": loss, "error_rate": errors}

    for node in monitored_nodes():
        n = noise.node[node]
        cpu_base = _node_cpu_base(node)
        cpu = np.clip(_apply(cpu_base + 0.25 * cpu_base * diurnal + n["cpu_pct"], shaping, node, "cpu_pct"), 0, 100)
        mem = noise.memory_base[node] + 0.002 * idx + n["memory_pct"]
        mem = np.clip(_apply(mem, shaping, node, "memory_pct"), 0, 100)
        out.nodes[node] = {"cpu_pct": cpu, "memory_pct": mem}

    for svc in SERVICES:
        sid = svc["service_id"]
        n = noise.service[sid]
        lat = np.full(N_SAMPLES, svc["base_latency_ms"])
        delivery = np.ones(N_SAMPLES)
        for lid in svc["probe_path"]:
            link_def = next(item for item in LINKS if item["link_id"] == lid)
            _, base_lat = _link_profile(link_def["a"], link_def["b"])
            lat = lat + np.maximum(0, out.links[lid]["latency_ms"] - base_lat)
            delivery = delivery * (1 - out.links[lid]["packet_loss_pct"] / 100)
        for dep in svc["depends_on"]:
            if dep in out.nodes:
                lat = lat + 1.0 * np.maximum(0, out.nodes[dep]["cpu_pct"] - 90)
        lat = _apply(lat + n["service_latency_ms"], shaping, sid, "service_latency_ms")
        success = 100 * delivery - 0.05 + n["service_success_pct"]
        success = np.clip(_apply(success, shaping, sid, "service_success_pct"), 0, 100)
        out.services[sid] = {"service_latency_ms": lat, "service_success_pct": success}

    # Measurement glitches overwrite readings last and are not physically clipped.
    for f in set_faults:
        series = out.series(f.entity, f.metric)
        series[:] = _apply(series, [f], f.entity, f.metric)
    return out


# --------------------------------------------------------------------------
# Ground truth from counterfactual comparison
# --------------------------------------------------------------------------


def _material(metric: str, clean: np.ndarray, faulted: np.ndarray) -> np.ndarray:
    diff = np.abs(faulted - clean)
    if metric == "utilization_pct":
        return diff >= 15
    if metric == "latency_ms":
        return diff >= np.maximum(1.5, 0.5 * np.abs(clean))
    if metric == "packet_loss_pct":
        return diff >= 0.5
    if metric == "error_rate":
        return diff >= 5
    if metric == "cpu_pct":
        return diff >= 20
    if metric == "memory_pct":
        return diff >= 12
    if metric == "service_latency_ms":
        return diff >= np.maximum(5.0, 0.25 * np.abs(clean))
    if metric == "service_success_pct":
        return diff >= 1.0
    raise ValueError(metric)


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    """Fill runs of False no longer than ``max_gap`` that sit between True samples."""
    out = mask.copy()
    true_idx = np.flatnonzero(mask)
    for a, b in zip(true_idx[:-1], true_idx[1:], strict=True):
        if 1 < b - a <= max_gap + 1:
            out[a:b] = True
    return out


def truth_intervals(
    clean: Rendered, faulted: Rendered, timestamps: list[datetime], min_len: int = 2, max_gap: int = 3
) -> list[dict]:
    """Material deviations as intervals; gaps up to ``max_gap`` samples (15 min) are bridged."""
    intervals = []
    for table_name in ("links", "nodes", "services"):
        clean_t, faulted_t = getattr(clean, table_name), getattr(faulted, table_name)
        for entity in sorted(faulted_t):
            for metric in sorted(faulted_t[entity]):
                mask = _close_gaps(_material(metric, clean_t[entity][metric], faulted_t[entity][metric]), max_gap)
                i = 0
                while i < N_SAMPLES:
                    if not mask[i]:
                        i += 1
                        continue
                    j = i
                    while j < N_SAMPLES and mask[j]:
                        j += 1
                    if j - i >= min_len:
                        intervals.append(
                            {
                                "entity_id": entity,
                                "metric": metric,
                                "start": timestamps[i].isoformat(),
                                "end": timestamps[j - 1].isoformat(),
                            }
                        )
                    i = j
    return intervals


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def to_frames(
    rendered: Rendered,
    timestamps: list[datetime],
    issues: list[DataIssue],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    ts = pd.DatetimeIndex(timestamps)
    frames = {}
    for name, table, metrics in (
        ("links", rendered.links, LINK_METRICS),
        ("nodes", rendered.nodes, NODE_METRICS),
        ("services", rendered.services, SERVICE_METRICS),
    ):
        parts = []
        for entity in sorted(table):
            df = pd.DataFrame({"timestamp": ts, "entity_id": entity})
            for metric in metrics:
                df[metric] = np.round(table[entity][metric], 3)
            df["delay_s"] = rng.integers(5, 60, N_SAMPLES)
            keep = np.ones(N_SAMPLES, dtype=bool)
            for issue in issues:
                if entity not in issue.entities:
                    continue
                window = slice(issue.start, min(issue.end, N_SAMPLES))
                if issue.kind == "gap":
                    keep[window] = False
                else:
                    df.loc[df.index[window], "delay_s"] = issue.delay_s
            parts.append(df[keep])
        frames[name] = pd.concat(parts, ignore_index=True).sort_values(["timestamp", "entity_id"], ignore_index=True)
    return frames
