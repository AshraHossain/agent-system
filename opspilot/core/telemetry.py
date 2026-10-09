"""Telemetry statistics: retrieval, baseline comparison, anomaly summary.

All numbers that reach an agent are computed here. Verdict rules are explicit
and listed in METRIC_RULES; the rule text is attached to every result.

Assessment value: the median of the most recent 6 samples (30 minutes) in the
window — robust to single spikes and reflects the current state even when a
fault started part-way through the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from opspilot.contracts.evidence import Evidence, EvidenceKind
from opspilot.contracts.request import TimeWindow
from opspilot.contracts.telemetry import (
    Anomaly,
    AnomalySummary,
    BaselineComparison,
    DataQualityIssue,
    MetricStats,
    TelemetrySnapshot,
)
from opspilot.core.dataset import Dataset, fmt_ts, parse_ts
from opspilot.core.errors import InvalidArgument, NotFound

RECENT_SAMPLES = 6
EPISODE_SAMPLES = 3  # 15 min of consecutive breaches counts as an episode
MIN_COVERAGE = 0.5
MAX_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Rule:
    kind: str  # "absolute" | "relative"
    warn: float
    crit: float
    min_delta: float = 0.0  # absolute rules: required rise over baseline
    min_z: float = 3.0  # relative rules

    def describe(self, metric: str) -> str:
        if self.kind == "absolute":
            return (
                f"{metric}: elevated if current >= {self.warn}, critical if >= {self.crit}, "
                f"and current - baseline_mean >= {self.min_delta}"
            )
        return (
            f"{metric}: elevated if +{self.warn:.0f}% over baseline mean, critical if "
            f"+{self.crit:.0f}%, and z >= {self.min_z}"
        )


METRIC_RULES: dict[str, Rule] = {
    "utilization_pct": Rule("absolute", 80, 90, min_delta=10),
    "cpu_pct": Rule("absolute", 80, 90, min_delta=15),
    "mem_pct": Rule("absolute", 85, 95, min_delta=10),
    "session_util_pct": Rule("absolute", 80, 90, min_delta=15),
    "packet_loss_pct": Rule("absolute", 0.3, 1.0, min_delta=0.2),
    "probe_loss_pct": Rule("absolute", 0.3, 1.0, min_delta=0.2),
    "error_rate": Rule("absolute", 5, 20, min_delta=4),
    "error_pct": Rule("absolute", 1.0, 2.0, min_delta=0.5),
    "latency_ms": Rule("relative", 50, 100),
    "query_latency_ms": Rule("relative", 50, 100),
    "latency_p95_ms": Rule("relative", 20, 50),
}


def validate_window(window: TimeWindow) -> TimeWindow:
    if window.end <= window.start:
        raise InvalidArgument("window end must be after start")
    if window.end - window.start > MAX_WINDOW:
        raise InvalidArgument("window longer than 24h is not allowed")
    return window


def _stats(values: pd.Series, expected: int, last_ts: str | None) -> MetricStats:
    if values.empty:
        return MetricStats(count=0, expected_count=expected, missing_count=expected)
    arr = values.to_numpy(dtype=float)
    return MetricStats(
        count=len(arr),
        expected_count=expected,
        missing_count=max(0, expected - len(arr)),
        mean=round(float(arr.mean()), 4),
        p95=round(float(np.percentile(arr, 95)), 4),
        min=round(float(arr.min()), 4),
        max=round(float(arr.max()), 4),
        last_ts=last_ts,
    )


def _expected(window: TimeWindow, interval_min: int) -> int:
    return int((window.end - window.start).total_seconds() // (interval_min * 60))


def _evaluate(
    metric: str, base: pd.Series, cur: pd.Series
) -> tuple[str, float | None, float | None, float | None]:
    """Return (verdict, assessed, delta_pct, zscore)."""
    rule = METRIC_RULES.get(metric)
    if rule is None or base.empty or cur.empty:
        return "insufficient_data", None, None, None
    assessed = float(np.median(cur.to_numpy()[-RECENT_SAMPLES:]))
    bmean = float(base.mean())
    bstd = float(base.std(ddof=0)) or 1e-9
    floor = max(bstd, abs(bmean) * 0.01, 1e-6)
    z = (assessed - bmean) / floor
    delta_pct = (assessed - bmean) / bmean * 100 if abs(bmean) > 1e-9 else None
    verdict = "normal"
    if rule.kind == "absolute":
        if assessed - bmean >= rule.min_delta:
            if assessed >= rule.crit:
                verdict = "critical"
            elif assessed >= rule.warn:
                verdict = "elevated"
    else:
        if delta_pct is not None and z >= rule.min_z:
            if delta_pct >= rule.crit:
                verdict = "critical"
            elif delta_pct >= rule.warn:
                verdict = "elevated"
    if verdict == "normal" and _episode(rule, cur.to_numpy(), bmean, floor) is not None:
        verdict = "recovered"
    return verdict, assessed, delta_pct, z


def _breaches(rule: Rule, values: np.ndarray, bmean: float, floor: float) -> np.ndarray:
    if rule.kind == "absolute":
        return (values >= rule.warn) & (values - bmean >= rule.min_delta)
    return (values >= bmean * (1 + rule.warn / 100)) & ((values - bmean) / floor >= rule.min_z)


def _episode(rule: Rule, values: np.ndarray, bmean: float, floor: float) -> tuple[int, int] | None:
    """Longest run of >= EPISODE_SAMPLES consecutive breaching samples, as (start, end) indices."""
    best, start = None, None
    hits = _breaches(rule, values, bmean, floor)
    for i, hit in enumerate([*hits, False]):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            if i - start >= EPISODE_SAMPLES and (best is None or i - start > best[1] - best[0]):
                best = (start, i - 1)
            start = None
    return best


def _last_seen(metric: str, cur: pd.DataFrame, bmean: float) -> str | None:
    rule = METRIC_RULES[metric]
    threshold = rule.warn if rule.kind == "absolute" else bmean * (1 + rule.warn / 100)
    hit = cur[cur["value"] >= threshold]
    return None if hit.empty else str(hit["ts"].iloc[-1])


def _first_seen(metric: str, cur: pd.DataFrame, bmean: float) -> str | None:
    rule = METRIC_RULES[metric]
    threshold = rule.warn if rule.kind == "absolute" else bmean * (1 + rule.warn / 100)
    hit = cur[cur["value"] >= threshold]
    return None if hit.empty else str(hit["ts"].iloc[0])


def compare_to_baseline(
    ds: Dataset, entity_id: str, metric: str, window: TimeWindow, baseline: TimeWindow
) -> BaselineComparison:
    validate_window(window)
    validate_window(baseline)
    catalog = ds.metric_catalog()
    if entity_id not in catalog:
        raise NotFound(f"no telemetry for entity {entity_id!r}")
    if metric not in catalog[entity_id]:
        raise NotFound(f"entity {entity_id} has no metric {metric!r}; has {catalog[entity_id]}")
    frame = ds.telemetry([entity_id], [metric], baseline.start, window.end)
    return _compare(ds, frame, entity_id, metric, window, baseline)


def _compare(ds, frame, entity_id, metric, window, baseline) -> BaselineComparison:
    w0, b0 = fmt_ts(window.start), fmt_ts(baseline.start)
    base = frame[(frame["ts"] >= b0) & (frame["ts"] < fmt_ts(baseline.end))]
    cur = frame[(frame["ts"] >= w0) & (frame["ts"] < fmt_ts(window.end))].sort_values("ts")
    exp_w, exp_b = _expected(window, ds.interval_min), _expected(baseline, ds.interval_min)
    bstats = _stats(base["value"], exp_b, None if base.empty else str(base["ts"].max()))
    wstats = _stats(cur["value"], exp_w, None if cur.empty else str(cur["ts"].max()))
    verdict, assessed, delta, z = _evaluate(metric, base["value"], cur["value"])
    if wstats.count < exp_w * MIN_COVERAGE or bstats.count < exp_b * MIN_COVERAGE:
        verdict = "insufficient_data"
    rule = METRIC_RULES.get(metric)
    rule_text = rule.describe(metric) if rule else f"{metric}: no rule defined"
    summary = (
        f"{entity_id} {metric} {verdict}: current {assessed:.3g} vs baseline mean {bstats.mean:.3g}"
        if assessed is not None and bstats.mean is not None
        else f"{entity_id} {metric} {verdict}: {wstats.count}/{exp_w} samples in window"
    )
    if delta is not None and assessed is not None:
        summary += f" ({delta:+.0f}%)"
    ev = Evidence.make(
        EvidenceKind.TELEMETRY if verdict != "insufficient_data" else EvidenceKind.DATA_QUALITY,
        summary,
        f"telemetry:{entity_id}/{metric}@{window.label()}",
        entity_id=entity_id,
        metric=metric,
        data={
            "verdict": verdict,
            "current": assessed,
            "baseline_mean": bstats.mean,
            "delta_pct": delta,
            "zscore": z,
            "samples": wstats.count,
        },
    )
    return BaselineComparison(
        entity_id=entity_id,
        metric=metric,
        baseline=bstats,
        window=wstats,
        delta_pct=None if delta is None else round(delta, 2),
        zscore=None if z is None else round(z, 2),
        verdict=verdict,
        rule=rule_text,
        evidence=[ev],
    )


def get_telemetry(
    ds: Dataset, entity_id: str, metrics: list[str] | None, window: TimeWindow
) -> TelemetrySnapshot:
    validate_window(window)
    catalog = ds.metric_catalog()
    if entity_id not in catalog:
        raise NotFound(f"no telemetry for entity {entity_id!r}")
    wanted = metrics or catalog[entity_id]
    unknown = [m for m in wanted if m not in catalog[entity_id]]
    if unknown:
        raise InvalidArgument(f"unknown metrics for {entity_id}: {unknown}")
    frame = ds.telemetry([entity_id], wanted, window.start, window.end)
    exp = _expected(window, ds.interval_min)
    out: dict[str, MetricStats] = {}
    for m in wanted:
        sub = frame[frame["metric"] == m]
        out[m] = _stats(sub["value"], exp, None if sub.empty else str(sub["ts"].max()))
    ev = Evidence.make(
        EvidenceKind.TELEMETRY,
        f"{entity_id} window stats for {', '.join(wanted)}",
        f"telemetry:{entity_id}/stats@{window.label()}",
        entity_id=entity_id,
        data={f"{m}_mean": s.mean for m, s in list(out.items())[:8]},
    )
    return TelemetrySnapshot(entity_id=entity_id, window=window.label(), metrics=out, evidence=[ev])


def _data_quality(
    ds: Dataset, frame: pd.DataFrame, entity: str, metrics: list[str], window: TimeWindow
) -> list[tuple[DataQualityIssue, Evidence]]:
    issues = []
    exp = _expected(window, ds.interval_min)
    cur = frame[(frame["ts"] >= fmt_ts(window.start)) & (frame["ts"] < fmt_ts(window.end))]
    lag_tolerance = timedelta(minutes=2 * ds.interval_min)
    last_by_metric = {m: cur[cur["metric"] == m]["ts"].max() for m in metrics}
    counts = {m: int((cur["metric"] == m).sum()) for m in metrics}
    all_delayed = all(
        isinstance(last_by_metric[m], str)
        and window.end - parse_ts(last_by_metric[m]) > lag_tolerance
        for m in metrics
    )
    if all_delayed and metrics:
        latest = max(parse_ts(v) for v in last_by_metric.values())
        lag = int((window.end - latest).total_seconds() // 60)
        ev = Evidence.make(
            EvidenceKind.DATA_QUALITY,
            f"{entity} telemetry delayed: latest sample {fmt_ts(latest)} ({lag} min behind)",
            f"telemetry:{entity}/freshness@{window.label()}",
            entity_id=entity,
            data={"lag_min": lag, "issue": "delayed"},
        )
        issues.append(
            (
                DataQualityIssue(
                    entity_id=entity,
                    metric=None,
                    issue="delayed",
                    detail=ev.summary,
                    evidence_id=ev.evidence_id,
                ),
                ev,
            )
        )
        return issues
    for m in metrics:
        missing = exp - counts[m]
        if missing >= 3:
            ev = Evidence.make(
                EvidenceKind.DATA_QUALITY,
                f"{entity} {m}: {missing}/{exp} samples missing in the investigation window",
                f"telemetry:{entity}/{m}/completeness@{window.label()}",
                entity_id=entity,
                metric=m,
                data={"missing": missing, "expected": exp, "issue": "missing"},
            )
            issues.append(
                (
                    DataQualityIssue(
                        entity_id=entity,
                        metric=m,
                        issue="missing",
                        detail=ev.summary,
                        evidence_id=ev.evidence_id,
                    ),
                    ev,
                )
            )
    return issues


def summarize_anomalies(
    ds: Dataset,
    window: TimeWindow,
    baseline: TimeWindow,
    entity_ids: list[str] | None = None,
) -> AnomalySummary:
    """Compare every metric of the selected (default: all) entities to baseline."""
    validate_window(window)
    validate_window(baseline)
    catalog = ds.metric_catalog()
    entities = entity_ids or sorted(catalog)
    unknown = [e for e in entities if e not in catalog]
    frame = ds.telemetry(
        [e for e in entities if e in catalog] or None, None, baseline.start, window.end
    )
    types = {cid: c["type"] for cid, c in ds.components.items()}
    anomalies: list[Anomaly] = []
    dq: list[DataQualityIssue] = []
    evidence: list[Evidence] = []
    normal = 0
    for entity in entities:
        if entity not in catalog:
            continue
        ef = frame[frame["entity_id"] == entity]
        for issue, ev in _data_quality(ds, ef, entity, catalog[entity], window):
            dq.append(issue)
            evidence.append(ev)
        for metric in catalog[entity]:
            mf = ef[ef["metric"] == metric]
            cmp = _compare(ds, mf, entity, metric, window, baseline)
            if cmp.verdict in ("elevated", "critical", "recovered"):
                ev = cmp.evidence[0]
                evidence.append(ev)
                cur = mf[mf["ts"] >= fmt_ts(window.start)].sort_values("ts")
                anomalies.append(
                    Anomaly(
                        entity_id=entity,
                        entity_type=types.get(entity, "unknown"),
                        metric=metric,
                        verdict=cmp.verdict,
                        baseline_mean=cmp.baseline.mean or 0.0,
                        window_mean=cmp.window.mean or 0.0,
                        peak=cmp.window.max or 0.0,
                        delta_pct=cmp.delta_pct,
                        zscore=cmp.zscore,
                        first_seen=_first_seen(metric, cur, cmp.baseline.mean or 0.0),
                        last_seen=_last_seen(metric, cur, cmp.baseline.mean or 0.0)
                        if cmp.verdict == "recovered"
                        else None,
                        rule=cmp.rule,
                        evidence_id=ev.evidence_id,
                    )
                )
            elif cmp.verdict == "normal":
                normal += 1
    for u in unknown:
        ev = Evidence.make(
            EvidenceKind.DATA_QUALITY,
            f"no telemetry exists for {u}",
            f"telemetry:{u}/catalog",
            entity_id=u,
            data={"issue": "unknown"},
        )
        evidence.append(ev)
        dq.append(
            DataQualityIssue(
                entity_id=u,
                metric=None,
                issue="unknown_entity",
                detail=ev.summary,
                evidence_id=ev.evidence_id,
            )
        )
    anomalies.sort(key=lambda a: (a.verdict != "critical", a.entity_id, a.metric))
    return AnomalySummary(
        window=window.label(),
        entities_checked=len(entities) - len(unknown),
        anomalies=anomalies,
        normal_metrics_checked=normal,
        data_quality=dq,
        evidence=evidence,
    )


def default_windows(reported_at: datetime) -> tuple[TimeWindow, TimeWindow]:
    window = TimeWindow(start=reported_at - timedelta(minutes=120), end=reported_at)
    baseline = TimeWindow(
        start=reported_at - timedelta(minutes=480), end=reported_at - timedelta(minutes=120)
    )
    return window, baseline
