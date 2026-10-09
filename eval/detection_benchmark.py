"""Detection-layer benchmark: anomaly precision/recall against ground-truth intervals.

    uv run python -m eval.detection_benchmark

This scorer reads ground-truth labels, so it lives in ``eval/``, never in
``netpulse``. Detection runs first, on investigator-visible data only (rows
not yet ingested at ``submitted_at`` are dropped). Labels are read only
afterwards, for scoring.

Matching rules (per case, per entity+metric):

* A predicted *confirmed* anomaly is a true positive if it overlaps a truth
  interval, allowing ``tolerance`` on each side.
* Recall counts truth intervals that are observable at submission time and
  overlapped by at least one confirmed prediction.
* Truth intervals hidden by gaps or delays are excluded from recall. They
  are reported separately, since no detector can see them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from netpulse.data.schemas import TABLE_METRICS, TelemetryQuery
from netpulse.data.store import DatasetStore
from netpulse.detection import DetectionConfig, DetectionRequest, detect_series

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CaseResult:
    case_id: str
    scenario: str
    predicted: int = 0
    true_positive_predictions: int = 0
    truth_observable: int = 0
    truth_detected: int = 0
    truth_unobservable: int = 0
    unconfirmed_signals: int = 0
    false_positives: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)


def detect_case(case: dict, store: DatasetStore, config: DetectionConfig | None = None) -> tuple[list, int]:
    """Return (confirmed anomalies, unconfirmed count) for one case using visible data only."""
    sub = case["submission"]
    window = store.telemetry_window(
        TelemetryQuery(
            dataset_id=sub["dataset_id"],
            window_start=sub["window_start"],
            window_end=sub["window_end"],
            as_of=case["submitted_at"],
        )
    )
    confirmed, unconfirmed = [], 0
    for table, metrics in TABLE_METRICS.items():
        for entity_id in window.entities(table):
            for metric in metrics:
                request = DetectionRequest(
                    entity_id=entity_id, metric=metric, window_start=sub["window_start"], window_end=sub["window_end"]
                )
                report = detect_series(request, *window.series(entity_id, metric), config)
                confirmed += report.confirmed
                unconfirmed += len(report.anomalies) - len(report.confirmed)
    return confirmed, unconfirmed


def _overlaps(a_start, a_end, b_start, b_end, tolerance: pd.Timedelta) -> bool:
    return a_start <= b_end + tolerance and b_start <= a_end + tolerance


def score_case(case: dict, label: dict, anomalies: list, unconfirmed: int, tolerance: pd.Timedelta) -> CaseResult:
    result = CaseResult(case_id=case["case_id"], scenario=label["scenario"], unconfirmed_signals=unconfirmed)
    truth = defaultdict(list)
    for iv in label["anomaly_intervals"]:
        if iv["observable_at_submission"]:
            truth[(iv["entity_id"], iv["metric"])].append((pd.Timestamp(iv["start"]), pd.Timestamp(iv["end"])))
            result.truth_observable += 1
        else:
            result.truth_unobservable += 1
    hit: set[tuple] = set()
    for a in anomalies:
        key = (a.entity_id, a.metric.value)
        result.predicted += 1
        matched = [(key, s, e) for s, e in truth.get(key, []) if _overlaps(a.start, a.end, s, e, tolerance)]
        if matched:
            result.true_positive_predictions += 1
            hit.update(matched)
        else:
            result.false_positives.append(f"{a.entity_id}:{a.metric.value}@{a.start:%H:%M}")
    for key, spans in truth.items():
        for s, e in spans:
            if (key, s, e) in hit:
                result.truth_detected += 1
            else:
                result.missed.append(f"{key[0]}:{key[1]}@{s:%H:%M}")
    return result


def run(
    data_root: Path = ROOT / "data/synthetic/v1",
    cases_file: Path = ROOT / "eval/datasets/v1/cases.jsonl",
    labels_file: Path = ROOT / "eval/labels/v1/labels.jsonl",
    config: DetectionConfig | None = None,
    tolerance_minutes: float = 10.0,
) -> dict:
    cases = [json.loads(line) for line in cases_file.read_text().splitlines() if line.strip()]
    # Detect on every case before any label is read.
    store = DatasetStore(data_root)
    detections = {c["case_id"]: detect_case(c, store, config) for c in cases}
    labels = {lab["case_id"]: lab for lab in map(json.loads, labels_file.read_text().splitlines())}
    tolerance = pd.Timedelta(minutes=tolerance_minutes)
    results = [score_case(c, labels[c["case_id"]], *detections[c["case_id"]], tolerance) for c in cases]

    predicted = sum(r.predicted for r in results)
    tp = sum(r.true_positive_predictions for r in results)
    observable = sum(r.truth_observable for r in results)
    detected = sum(r.truth_detected for r in results)
    return {
        "precision": tp / predicted if predicted else 1.0,
        "recall": detected / observable if observable else 1.0,
        "predicted": predicted,
        "true_positive_predictions": tp,
        "truth_observable": observable,
        "truth_detected": detected,
        "truth_unobservable": sum(r.truth_unobservable for r in results),
        "unconfirmed_signals": sum(r.unconfirmed_signals for r in results),
        "cases": [r.__dict__ for r in sorted(results, key=lambda r: r.scenario)],
    }


def main() -> int:
    report = run()
    print(
        f"precision {report['precision']:.3f}  recall {report['recall']:.3f}  "
        f"(predicted {report['predicted']}, observable truth {report['truth_observable']}, "
        f"unobservable {report['truth_unobservable']}, unconfirmed signals {report['unconfirmed_signals']})"
    )
    print(f"{'scenario':<26}{'pred':>5}{'tp':>4}{'truth':>6}{'hit':>4}  false positives / missed")
    for r in report["cases"]:
        print(
            f"{r['scenario']:<26}{r['predicted']:>5}{r['true_positive_predictions']:>4}"
            f"{r['truth_observable']:>6}{r['truth_detected']:>4}  FP={r['false_positives']} MISS={r['missed']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
