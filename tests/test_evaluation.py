"""Eval harness: scorer unit tests and the CI regression gate on the committed synthetic cases."""

import json
from functools import cache

import pytest

from eval import report as eval_report
from eval.investigation_benchmark import aggregate, failure_analysis, run


@cache
def heuristic_run() -> dict:
    return run("heuristic")


def test_regression_gate_passes_on_committed_data():
    result = heuristic_run()
    limits = json.loads(eval_report.THRESHOLDS.read_text())
    from eval import detection_benchmark

    failures = eval_report.check_thresholds(result["metrics"], detection_benchmark.run(), limits)
    assert failures == []


def test_nothing_is_ever_executed_and_every_case_scored():
    result = heuristic_run()
    assert result["metrics"]["cases"] == 20
    assert result["metrics"]["executed_actions"] == 0
    assert result["generators_used"] == ["heuristic"]


def test_gate_flags_regressions():
    metrics = dict(heuristic_run()["metrics"], root_cause_top1=0.5, executed_actions=1)
    detection = {"precision": 0.99, "recall": 0.5}
    failures = eval_report.check_thresholds(metrics, detection, json.loads(eval_report.THRESHOLDS.read_text()))
    joined = "\n".join(failures)
    assert "root_cause_top1" in joined and "executed_actions" in joined and "detection_recall" in joined


def test_failure_analysis_names_the_problem():
    row = {
        "outcome_correct": False, "outcome": "inconclusive", "expected_outcome": "root_cause", "top1": False,
        "predicted_top": ["unknown@x"], "expected_causes": ["link_degradation@y"], "top3": False,
        "predicted_escalate": True, "should_escalate": False, "invalid_citations": ["hyp-01->ev-x"],
        "missing_actions": ["check_optic_levels"], "errors": 1,
    }  # fmt: skip
    notes = " | ".join(failure_analysis(row))
    for needle in ("outcome", "top-1 miss", "unneeded escalation", "invalid citations", "check_optic_levels", "errors"):
        assert needle in notes


def test_aggregate_handles_cases_without_root_cause():
    rows = [r for r in heuristic_run()["cases"] if r["top1"] is None]
    assert rows, "dataset must contain cases with no labeled root cause"
    assert aggregate(rows)["root_cause_top1"] == 1.0


def test_report_files_are_named_by_dataset_provider_sha(tmp_path):
    result = heuristic_run()
    report = {
        "dataset": "v1", "provider": "heuristic", "git_sha": "abc1234", "notice": eval_report.NOTICE,
        "generators_used": result["generators_used"], "detection": {
            "precision": 1, "recall": 1, "predicted": 0, "truth_observable": 0,
            "truth_unobservable": 0, "unconfirmed_signals": 0,
        },
        "investigation": result["metrics"], "cases": result["cases"],
    }  # fmt: skip
    json_path, md_path = eval_report.write_report(report, [], tmp_path)
    assert json_path.name == "v1-heuristic-abc1234.json" and md_path.name == "v1-heuristic-abc1234.md"
    assert "SYNTHETIC DATA" in md_path.read_text()
    assert json.loads(json_path.read_text())["provider"] == "heuristic"


@pytest.mark.parametrize("scenario_case", ["case-05", "case-10"])
def test_known_hard_cases_are_reported_honestly(scenario_case):
    row = next(r for r in heuristic_run()["cases"] if r["case_id"] == scenario_case)
    assert row["failures"], "hard cases are expected to show up in the failure analysis"
