# Deterministic detection

Package: [`netpulse/detection/`](../netpulse/detection/). No LLM is involved.
Every number that reaches the investigator as evidence is produced here.

## Contract

```python
report = detect_series(
    DetectionRequest(entity_id, metric, window_start, window_end),  # input schema, validated
    timestamps,   # tz-aware, strictly increasing; include ≥24 h of history before the window
    values,       # numeric; NaN = missing sample
    config=DetectionConfig(),  # optional
)  # -> SeriesDetectionReport
```

| Output field | Meaning |
|---|---|
| `window_coverage`, `reference_coverage` | Expected vs. present points, gaps, and the longest gap. Missing data is **reported, never imputed**. |
| `intervals` | Raw per-method findings (`DetectedInterval`): start, end, peak, baseline, threshold, score. |
| `anomalies` | Same-direction intervals from different methods merged into `ConsolidatedAnomaly` records. `confirmed` is true when ≥ `min_methods` (default 2) independent methods agree, or the reading is physically impossible. |
| `comparison` | Window median vs. the same window 24 h earlier (`WindowComparison`). |
| `skipped` | Methods not run, with the reason (`no_data_in_window`, `insufficient_history`, `no_comparison_window`). |
| `failures` | A method that raised at runtime. The other methods still run. |

Malformed input raises `DetectionInputError`: naive timestamps, duplicate or
unsorted timestamps, length mismatch, non-numeric or infinite values.
`to_evidence(anomaly, evidence_id)` renders an `EvidenceItem`. The text and
numeric fields in it are copied from the detector output.

## Methods

| Method | Flags a sample when… | Handles |
|---|---|---|
| `static_threshold` | it is beyond the operator alarm level (e.g. utilization > 85%, success < 99%) | absolute limits |
| `plausibility` | it is physically impossible (utilization > 100%, negative counters). One sample is enough. | measurement faults |
| `rolling_baseline` | it deviates from the median of (t − 1 h − 2 h, t − 1 h] by more than max(4σ, floor) | recent level shifts. The 1 h lag keeps a ramping fault out of its own baseline. |
| `robust_zscore` | its median/MAD z-score vs. the previous 24 h is ≥ 3.5 **and** it exceeds the effect floor | outliers, robust to past spikes. A flat reference gives a capped z, not infinity. |
| `window_comparison` | it differs from the same timestamp yesterday by more than max(4·√2·σ, floor) | removes the daily cycle |

σ is the per-sample noise. It is estimated robustly from first differences
of the reference period, which removes slow trends such as the daily cycle.
The **effect floor** (`min_abs`, `min_rel` per metric) stops statistically
significant but operationally trivial changes from being flagged.

**Interval rules.** Flagged samples merge when they are at most 10 minutes
apart, and an interval needs at least 2 samples (except plausibility). A data
gap longer than the merge distance splits an interval, so an anomaly is never
inferred across missing data.

## Measured performance (dataset v1, `python -m eval.detection_benchmark`)

| Metric | Value |
|---|---|
| Precision (confirmed anomalies) | 0.941 (64 of 68) |
| Recall (observable truth intervals) | 1.000 (64 of 64) |
| Truth intervals hidden by gaps or delays | 1 (excluded from recall and reported separately) |
| Unconfirmed single-method signals | 38 (kept as weak signals, not counted as predictions) |
| Normal and noisy-normal cases | 0 confirmed anomalies |

All 4 false positives are service-latency rises that are real knock-on
effects of a fault (congestion, edge saturation, sub-threshold symptoms). They
exceed the detector's floor (3 ms or 15%) but not the ground truth's
materiality rule (5 ms or 25%). They were **left as they are**, because
moving the floor to match the labels would be fitting detectors to the
evaluation set.

`tests/test_detection_benchmark.py` enforces floors of precision ≥ 0.90 and
recall ≥ 0.95, with zero detections on normal cases.

## Limitations

- These results are on synthetic data that the author of the detectors also
  generated. Real telemetry has seasonality, level shifts, and noise
  structure that this data lacks. Do not quote these numbers as real-world
  performance.
- There is one fixed configuration for all entities. Per-entity tuning,
  weekly seasonality, and change-point detection are not implemented.
- The 24-hour comparison assumes yesterday was normal. A fault that also
  occurred yesterday will be missed by that method; the others may still catch it.
- Detection runs per series. Correlation across entities is the job of
  topology analysis (Phase 5).
