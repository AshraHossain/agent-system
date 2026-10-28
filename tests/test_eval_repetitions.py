"""`opspilot eval --repeat N`: repetitions, spread, per-agent output compliance and,
for live runs, a mock baseline on the same cases. Offline throughout: a flaky
stand-in plays the live model."""

import asyncio
import json

import pytest

from opspilot import cli
from opspilot.adk import agents
from opspilot.adk.models import ScriptedLlm
from opspilot.eval.harness import run_eval, to_markdown

SCORES = (
    "status_accuracy",
    "escalation_accuracy",
    "root_cause_top1",
    "citation_validity",
    "tool_invocation_recall",
    "llm_calls_total",
)


@pytest.fixture(scope="module")
def twice(settings):
    # C11 injects a model timeout into the incident analyst.
    return asyncio.run(run_eval(settings, ["C03", "C11"], build_data=False, repeat=2))


def test_each_repetition_is_a_full_pass_over_the_cases(twice):
    assert twice["config"]["repetitions"] == 2
    runs = [(c["case_id"], c["repetition"]) for c in twice["cases"]]
    assert runs == [("C03", 1), ("C11", 1), ("C03", 2), ("C11", 2)]
    assert [(d["case_id"], d["repetition"]) for d in twice["details"]] == runs


def test_the_mock_does_not_vary_between_repetitions(twice):
    spread = twice["repetitions"]["spread"]
    for metric in SCORES:
        assert spread[metric]["stdev"] == 0 and spread[metric]["min"] == spread[metric]["max"]
    per_case = twice["repetitions"]["per_case"]
    assert per_case["C03"]["statuses"] == {"investigated": 2} and per_case["C03"]["top1_hits"] == 2
    assert per_case["C11"]["statuses"] == {"inconclusive": 2} and per_case["C11"]["fallbacks"] == 2


def test_the_agent_table_counts_fallbacks_and_clean_runs(twice):
    analyst = twice["agents"]["incident_analyst"]
    assert analyst["runs"] == 4 and analyst["clean_runs"] == 2 and analyst["clean_rate"] == 0.5
    assert analyst["fallbacks"] == {"invalid_output": 0, "budget": 0, "model_error": 2}
    others = [a for name, a in twice["agents"].items() if name != "incident_analyst"]
    assert len(others) == 5 and all(a["clean_runs"] == a["runs"] == 4 for a in others)


def test_the_report_shows_spread_agents_and_stability(twice):
    md = to_markdown(twice)
    assert "(scripted-mock), 2 repetitions" in md
    assert "| root_cause_top1 | 1 | 1 | 1 | 0 |" in md
    assert "## Output compliance by agent" in md
    assert "| incident_analyst | 4 | 8 | 0 | 0 | 0 / 0 / 2 | 2/4 |" in md
    assert "| C11 | inconclusive ×2 (inconclusive) | 2/2 | 2/2 |" in md
    assert "Live vs mock" not in md


def test_a_single_run_keeps_the_one_table_report(settings):
    res = asyncio.run(run_eval(settings, ["C03"], build_data=False))
    assert res["config"]["repetitions"] == 1 and "repetitions" not in res
    md = to_markdown(res)
    assert "| Metric | Value |" in md and "| C03 | " in md
    assert "## Output compliance by agent" in md


@pytest.mark.parametrize("repeat", [0, 21])
def test_repeat_is_bounded(settings, repeat):
    with pytest.raises(ValueError, match="between 1 and 20"):
        asyncio.run(run_eval(settings, ["C03"], build_data=False, repeat=repeat))


# --- live runs, played by a flaky stand-in --------------------------------------------


@pytest.fixture
def flaky_live(monkeypatch):
    """'Live' runs: prose from the analyst in the first run, one rejected answer from
    the topology analyst in every run. Mock runs are the real mock."""
    real = agents.make_model
    runs = {"n": 0}

    def make_model(agent, settings, faults=None):
        if settings.provider == "mock":
            return real(agent, settings, faults)
        runs["n"] += agent == "telemetry_analyst"  # built first, once per run
        broken = set()
        if agent == "incident_analyst" and runs["n"] == 1:
            broken.add("invalid_output")
        if agent == "topology_analyst":
            broken.add("invalid_args")
        return ScriptedLlm(agent=agent, faults=frozenset(broken), transient=True)

    monkeypatch.setattr(agents, "make_model", make_model)


def _live(settings):
    from dataclasses import replace

    return replace(settings, provider="gemini")


def test_a_live_run_is_compared_with_the_mock_on_the_same_cases(settings, flaky_live):
    res = asyncio.run(
        run_eval(_live(settings), ["C03", "C11"], build_data=False, repeat=2, baseline=True)
    )
    cfg = res["config"]
    assert cfg["provider"] == "gemini" and cfg["cases"] == ["C03"]
    assert cfg["skipped_cases"] == ["C11"]  # its model fault exists only in the mock

    analyst, topology = res["agents"]["incident_analyst"], res["agents"]["topology_analyst"]
    assert analyst["fallbacks"]["invalid_output"] == 1 and analyst["clean_runs"] == 1
    assert topology["schema_retries"] == topology["runs_with_schema_retries"] == 2
    assert topology["clean_runs"] == 0

    base = res["baseline"]
    assert base["provider"] == "mock" and [c["case_id"] for c in base["cases"]] == ["C03"]
    assert all(a["clean_runs"] == a["runs"] for a in base["agents"].values())
    case = base["comparison"]["cases"]["C03"]
    assert case["mock_status"] == "investigated" and case["mock_top1"] is True
    assert case["live"]["statuses"] == {"inconclusive": 1, "investigated": 1}
    status = base["comparison"]["metrics"]["status_accuracy"]
    assert status["mock"] == 1.0 and (status["live"]["min"], status["live"]["max"]) == (0, 1)

    md = to_markdown(res)
    assert "## Live vs mock (same cases)" in md
    assert "| status_accuracy | 1 | 0.5 | 0–1 |" in md
    assert "| C03 | investigated | inconclusive ×1, investigated ×1 | ✓ |" in md
    assert "skipped (faults only the mock can inject): C11" in md


def test_without_a_baseline_no_mock_runs(settings, flaky_live):
    res = asyncio.run(run_eval(_live(settings), ["C03"], build_data=False))
    assert "baseline" not in res and "Live vs mock" not in to_markdown(res)


# --- CLI ------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, data_dir, tmp_path):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPSPILOT_SESSION_DB", f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    return tmp_path


def test_cli_repeat_reports_progress_and_writes_the_results(env, capsys):
    assert cli.main(["eval", "--cases", "C01", "--repeat", "2", "--out", str(env)]) == 0
    captured = capsys.readouterr()
    assert "[1/2] C01 run 1/2: investigated" in captured.err
    assert "[2/2] C01 run 2/2: investigated" in captured.err
    assert "2 repetitions" in captured.out
    (path,) = env.glob("eval_mock_main_x2_*.json")
    assert json.loads(path.read_text())["config"]["repetitions"] == 2


def test_cli_live_eval_includes_the_mock_comparison(env, capsys, flaky_live):
    assert cli.main(["--live", "eval", "--cases", "C03", "--out", str(env)]) == 0
    out = capsys.readouterr().out
    assert "gemini (" in out and "## Live vs mock (same cases)" in out


@pytest.mark.parametrize("value", ["0", "21", "two"])
def test_cli_rejects_a_bad_repeat_count(capsys, value):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["eval", "--repeat", value])
    assert exit_info.value.code == 2
    assert "--repeat: must be a whole number from 1 to 20" in capsys.readouterr().err
