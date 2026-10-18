"""Labels-hidden agent evaluation runner.

Cases live in cases.yaml alongside this file.  The runner never exposes
expected values to the agents; they run against the feed state and the
results are compared afterwards.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from trafficflow.agents.pipeline import run_report_sync
from trafficflow.data.feed import SyntheticFeed

_DEFAULT_CASES = Path(__file__).with_name("cases.yaml")


def run_cases(
    cases_path: str | Path = _DEFAULT_CASES,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run all cases in `cases_path`, return summary dict."""
    with open(cases_path) as f:
        cases = yaml.safe_load(f)

    results = []
    for case in cases:
        cid = case["id"]
        now = datetime.fromisoformat(case["now_iso"])
        feed = SyntheticFeed(seed=case.get("feed_seed", 7), now=now)

        report = run_report_sync(feed)
        expected = case.get("expected", {})
        errors: list[str] = []

        # network_status exact match
        if "network_status" in expected:
            got = report.status.value
            want = expected["network_status"]
            if got != want:
                errors.append(f"status: expected {want!r}, got {got!r}")

        # min_segments_with_forecast
        if "min_segments_with_forecast" in expected:
            segs_with_fc = sum(
                1 for seg in feed.segments() if feed.history(seg.id, days=7).__len__() > 0
            )
            want = expected["min_segments_with_forecast"]
            if segs_with_fc < want:
                errors.append(f"segments_with_forecast: expected >= {want}, got {segs_with_fc}")

        # incident detection
        from trafficflow.core.anomaly import detect

        anomalies = [
            a for seg in feed.segments() for a in detect(feed.history(seg.id, days=7), seg.id)
        ]
        n_incidents = len(anomalies)

        if "incident_count" in expected:
            want = expected["incident_count"]
            if n_incidents != want:
                errors.append(f"incident_count: expected {want}, got {n_incidents}")

        if "incident_count_gte" in expected:
            want = expected["incident_count_gte"]
            if n_incidents < want:
                errors.append(f"incident_count_gte: expected >= {want}, got {n_incidents}")

        passed = len(errors) == 0
        entry: dict[str, Any] = {"id": cid, "passed": passed, "errors": errors}
        if verbose:
            entry["status"] = report.status.value
            entry["incidents"] = n_incidents
        results.append(entry)

        if verbose:
            mark = "PASS" if passed else "FAIL"
            print(f"[{mark}] {cid}: {case['description']}")
            for e in errors:
                print(f"       {e}")

    passed_count = sum(1 for r in results if r["passed"])
    return {
        "passed": passed_count,
        "failed": len(results) - passed_count,
        "results": results,
    }
