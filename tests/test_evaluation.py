"""Phase 10: evaluation regression tests (offline, deterministic).

Thresholds are strict because the offline pipeline is deterministic. They guard
tools, rules, orchestration and verification — not LLM reasoning quality.
"""

import asyncio

import pytest

from opspilot.eval.harness import run_eval, to_markdown
from opspilot.eval.perturbations import PERTURBATIONS, run_guardrail_eval

THRESHOLDS = {
    "completion_rate": 1.0,
    "status_accuracy": 1.0,
    "escalation_accuracy": 1.0,
    "root_cause_top1": 1.0,
    "root_cause_top3": 1.0,
    "summary_correctness": 1.0,
    "citation_validity": 1.0,
    "citation_relevance": 1.0,
    "runbook_precision": 0.9,
    "runbook_recall": 0.9,
    "missing_evidence_detection": 1.0,
    "malicious_document_handling": 1.0,
    "tool_invocation_recall": 1.0,
    "affected_services_jaccard": 0.95,
}


@pytest.fixture(scope="module")
def results(settings):
    return asyncio.run(run_eval(settings, build_data=False))


@pytest.mark.slow
def test_offline_eval_meets_thresholds(results):
    agg = results["aggregate"]
    for metric, minimum in THRESHOLDS.items():
        assert agg[metric] is not None and agg[metric] >= minimum, (metric, agg[metric])
    assert agg["unsupported_claim_rate"] == 0.0
    assert agg["false_alarm_rate"] == 0.0
    assert agg["tool_permission_violations"] == 0
    assert agg["irrelevant_runbooks_included"] == 0
    assert agg["tokens_total"] is None  # the mock reports no token usage — never invented


@pytest.mark.slow
def test_eval_records_configuration(results):
    cfg = results["config"]
    assert cfg["provider"] == "mock" and cfg["model"] == "scripted-mock"
    assert cfg["google_adk"] == "2.11.0" and len(cfg["cases"]) == 14
    assert "not LLM reasoning" in cfg["note"]
    assert "| root_cause_top1 |" in to_markdown(results)


@pytest.mark.slow
async def test_guardrails_detect_injected_failures(settings):
    res = await run_guardrail_eval(settings)
    missed = [r for r in res["results"] if not r["detected"]]
    assert not missed, missed
    assert len(res["results"]) == len(PERTURBATIONS) >= 8


def test_agents_never_receive_ground_truth(settings):
    """The runner passes only request text and dataset_id; labels stay in the harness."""
    import inspect

    from opspilot.adk import runner, tools

    src = inspect.getsource(runner) + inspect.getsource(tools)
    for leak in ("labels", "root_causes", "cases_spec", "get_case"):
        assert leak not in src
