"""Run every detector on one series, isolate failures, and consolidate findings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from netpulse.detection import algorithms as alg
from netpulse.detection.config import DetectionConfig
from netpulse.detection.schemas import (
    ConsolidatedAnomaly,
    DetectedInterval,
    DetectionRequest,
    DetectorFailure,
    SeriesDetectionReport,
    SkippedMethod,
    WindowComparison,
)
from netpulse.models import DetectorMethod, EvidenceItem, EvidenceSource

STATISTICAL = (DetectorMethod.ROLLING_BASELINE, DetectorMethod.ROBUST_ZSCORE, DetectorMethod.WINDOW_COMPARISON)
# Which method's baseline best describes "normal" for a consolidated anomaly, in order of preference.
BASELINE_PREFERENCE = (DetectorMethod.WINDOW_COMPARISON, DetectorMethod.ROLLING_BASELINE, DetectorMethod.ROBUST_ZSCORE)


def detect_series(
    request: DetectionRequest,
    timestamps: Sequence[datetime] | pd.DatetimeIndex,
    values: Sequence[float],
    config: DetectionConfig | None = None,
) -> SeriesDetectionReport:
    """Run all applicable detectors on one entity/metric series.

    ``timestamps``/``values`` should include history before the window: the
    reference period and the comparison lag. Input errors raise
    ``DetectionInputError``. A detector that fails at runtime is recorded in
    ``failures`` and the others still run.
    """
    cfg = config or DetectionConfig()
    policy = cfg.policies[request.metric]
    t, v = alg.to_arrays(timestamps, values)

    interval_ns = int(cfg.expected_interval_minutes * alg.NS_PER_MINUTE)
    max_gap_ns = int(cfg.max_gap_minutes * alg.NS_PER_MINUTE)
    ws, we = alg.dt_to_ns(request.window_start), alg.dt_to_ns(request.window_end)
    ref_start = ws - int(cfg.reference_hours * 60 * alg.NS_PER_MINUTE)

    window_mask = (t >= ws) & (t <= we)
    reference_mask = (t >= ref_start) & (t < ws)
    window_cov = alg.assess_coverage(t, ws, we, interval_ns)
    reference_cov = alg.assess_coverage(t, ref_start, ws - interval_ns, interval_ns)

    intervals: list[DetectedInterval] = []
    skipped: list[SkippedMethod] = []
    failures: list[DetectorFailure] = []
    comparison: WindowComparison | None = None

    def guarded(method: DetectorMethod, fn) -> None:
        try:
            intervals.extend(fn())
        except Exception as exc:  # noqa: BLE001 - one detector must not take down the others
            failures.append(DetectorFailure(method=method, error_type=type(exc).__name__, message=str(exc)[:500]))

    if not window_mask.any():
        all_methods = [DetectorMethod.STATIC_THRESHOLD, DetectorMethod.PLAUSIBILITY, *STATISTICAL]
        skipped = [SkippedMethod(method=m, reason="no_data_in_window") for m in all_methods]
        return SeriesDetectionReport(
            request=request, window_coverage=window_cov, reference_coverage=reference_cov, skipped=skipped
        )

    tw, vw = t[window_mask], v[window_mask]
    guarded(DetectorMethod.PLAUSIBILITY, lambda: alg.plausibility(tw, vw, policy, max_gap_ns=max_gap_ns))
    guarded(
        DetectorMethod.STATIC_THRESHOLD,
        lambda: alg.static_threshold(tw, vw, policy, min_points=cfg.min_points, max_gap_ns=max_gap_ns),
    )

    sigma = alg.noise_sigma(t[reference_mask], v[reference_mask], interval_ns)
    if reference_cov.coverage_ratio < cfg.min_reference_coverage or sigma is None:
        reason = f"insufficient_history (reference coverage {reference_cov.coverage_ratio:.0%})"
        skipped += [SkippedMethod(method=m, reason=reason) for m in STATISTICAL]
    else:
        window_points = cfg.rolling_window_minutes / cfg.expected_interval_minutes
        guarded(
            DetectorMethod.ROLLING_BASELINE,
            lambda: alg.rolling_baseline_detector(
                t,
                v,
                window_mask,
                policy,
                sigma,
                k=cfg.rolling_k,
                window_ns=int(cfg.rolling_window_minutes * alg.NS_PER_MINUTE),
                lag_ns=int(cfg.rolling_lag_minutes * alg.NS_PER_MINUTE),
                min_count=max(3, int(window_points * cfg.min_reference_coverage)),
                min_points=cfg.min_points,
                max_gap_ns=max_gap_ns,
            ),
        )
        guarded(
            DetectorMethod.ROBUST_ZSCORE,
            lambda: alg.robust_zscore(
                t,
                v,
                window_mask,
                reference_mask,
                policy,
                z_threshold=cfg.zscore_threshold,
                min_points=cfg.min_points,
                max_gap_ns=max_gap_ns,
            ),
        )

        def _compare() -> list[DetectedInterval]:
            nonlocal comparison
            found, comparison = alg.window_comparison(
                t,
                v,
                ws,
                we,
                policy,
                sigma,
                lag_ns=int(cfg.comparison_lag_hours * 60 * alg.NS_PER_MINUTE),
                k=cfg.comparison_k,
                min_points=cfg.min_points,
                max_gap_ns=max_gap_ns,
                min_pairs=max(1, int(window_cov.expected_points * cfg.min_reference_coverage)),
            )
            if comparison is None:
                skipped.append(SkippedMethod(method=DetectorMethod.WINDOW_COMPARISON, reason="no_comparison_window"))
            return found

        guarded(DetectorMethod.WINDOW_COMPARISON, _compare)

    intervals.sort(key=lambda i: (i.start, i.method))
    return SeriesDetectionReport(
        request=request,
        window_coverage=window_cov,
        reference_coverage=reference_cov,
        intervals=intervals,
        anomalies=consolidate(request, intervals, cfg),
        comparison=comparison,
        skipped=skipped,
        failures=failures,
    )


def consolidate(
    request: DetectionRequest, intervals: list[DetectedInterval], cfg: DetectionConfig
) -> list[ConsolidatedAnomaly]:
    """Merge overlapping same-direction intervals from different methods.

    An anomaly is ``confirmed`` when at least ``min_methods`` independent
    methods agree, or when a reading is physically impossible. Unconfirmed
    anomalies are kept as weak signals; they are not discarded.
    """
    gap = pd.Timedelta(minutes=cfg.max_gap_minutes)
    out: list[ConsolidatedAnomaly] = []
    for direction in ("high", "low"):
        group: list[DetectedInterval] = []
        end = None
        for item in sorted((i for i in intervals if i.direction == direction), key=lambda i: i.start):
            if group and item.start > end + gap:
                out.append(_merge(request, group, cfg))
                group = []
            end = item.end if not group else max(end, item.end)
            group.append(item)
        if group:
            out.append(_merge(request, group, cfg))
    return sorted(out, key=lambda a: (a.start, a.direction))


def _merge(request: DetectionRequest, group: list[DetectedInterval], cfg: DetectionConfig) -> ConsolidatedAnomaly:
    methods = sorted({i.method for i in group})
    independent = [m for m in methods if m != DetectorMethod.PLAUSIBILITY]
    implausible = any(i.implausible for i in group)
    direction = group[0].direction
    peak = max(group, key=lambda i: i.peak_value) if direction == "high" else min(group, key=lambda i: i.peak_value)
    reference = next((i for m in BASELINE_PREFERENCE for i in group if i.method == m), None)
    static = next((i for i in group if i.method == DetectorMethod.STATIC_THRESHOLD), None)
    threshold = static.threshold if static else (reference.threshold if reference else peak.threshold)
    return ConsolidatedAnomaly(
        entity_id=request.entity_id,
        metric=request.metric,
        start=min(i.start for i in group),
        end=max(i.end for i in group),
        direction=direction,
        peak_value=peak.peak_value,
        peak_at=peak.peak_at,
        baseline_value=reference.baseline_value if reference else None,
        threshold=threshold,
        methods=methods,
        confirmed=implausible or len(independent) >= cfg.min_methods,
        implausible=implausible,
        details=group,
    )


def to_evidence(anomaly: ConsolidatedAnomaly, evidence_id: str, config: DetectionConfig | None = None) -> EvidenceItem:
    """Render a consolidated anomaly as citable evidence. All numbers come from the detector output."""
    unit = (config or DetectionConfig()).policies[anomaly.metric].unit
    parts = [
        f"{anomaly.metric.value} on {anomaly.entity_id} {anomaly.direction}",
        f"{anomaly.start:%Y-%m-%d %H:%M}–{anomaly.end:%H:%M} UTC",
        f"peak {anomaly.peak_value:g} {unit} at {anomaly.peak_at:%H:%M}",
    ]
    if anomaly.baseline_value is not None:
        parts.append(f"baseline {anomaly.baseline_value:g} {unit}")
    if anomaly.threshold is not None:
        parts.append(f"threshold {anomaly.threshold:g} {unit}")
    parts.append("methods: " + ", ".join(m.value for m in anomaly.methods))
    if anomaly.implausible:
        parts.append("PHYSICALLY IMPLAUSIBLE READING (possible telemetry fault)")
    if not anomaly.confirmed:
        parts.append("unconfirmed (single method)")
    return EvidenceItem(
        evidence_id=evidence_id,
        source=EvidenceSource.DETECTOR,
        summary="; ".join(parts)[:600],
        entity_ids=[anomaly.entity_id],
        window_start=anomaly.start,
        window_end=anomaly.end,
        metric=anomaly.metric,
        observed_value=anomaly.peak_value,
        baseline_value=anomaly.baseline_value,
        threshold=anomaly.threshold,
        unit=unit,
        method="+".join(m.value for m in anomaly.methods),
    )
