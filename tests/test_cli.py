"""`opspilot` command line: investigate, list, show, resume, eval and error paths."""

import json
import re
from dataclasses import replace

import pytest

from opspilot import cli
from opspilot.adk.runtime import RunFaults
from opspilot.datasets.spec import get_case

REQUEST = get_case("C02")["request"]


@pytest.fixture
def env(monkeypatch, data_dir, tmp_path):
    """Point the CLI at the shared datasets and a throwaway session database."""
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPSPILOT_SESSION_DB", f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    return tmp_path


def _investigate(capsys, *extra):
    argv = ["investigate", "--dataset", "C02", "--request", REQUEST, *extra]
    assert cli.main(argv) == 0
    return capsys.readouterr().out


def _session_id(out):
    return re.search(r"session: (\S+) \(resume with", out).group(1)


# --- investigate / list / show / resume ----------------------------------------


def test_investigate_prints_report_and_session(env, capsys):
    out = _investigate(capsys)
    assert "6. FINAL INVESTIGATION REPORT" in out and "status: investigated" in out
    assert "LABELS" not in out  # labels are only ever shown for the demo
    assert _session_id(out)


def test_investigate_json_is_a_valid_report(env, capsys):
    report = json.loads(_investigate(capsys, "--json"))
    assert report["dataset_id"] == "C02" and report["status"] == "investigated"


def test_list_and_show_read_back_a_persisted_run(env, capsys):
    sid = _session_id(_investigate(capsys))
    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out.split()
    assert listing[:3] == [sid, "C02", "investigated"]
    assert cli.main(["show", sid]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "investigated" and shown["dataset_id"] == "C02"


def test_list_with_no_sessions_prints_nothing(env, capsys):
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out == ""


def test_resume_of_completed_run_does_no_model_work(env, capsys):
    sid = _session_id(_investigate(capsys))
    assert cli.main(["resume", sid]) == 0
    out = capsys.readouterr().out
    assert "run: 0 model calls, 0 tool calls" in out and sid in out


@pytest.mark.parametrize("command", ["show", "resume"])
def test_unknown_session_is_reported_on_stderr(env, capsys, command):
    assert cli.main([command, "does-not-exist"]) == 1
    captured = capsys.readouterr()
    assert "no session does-not-exist" in captured.err and captured.out == ""


def test_investigate_builds_missing_datasets_first(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(tmp_path / "fresh"))
    monkeypatch.setenv("OPSPILOT_SESSION_DB", f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    out = _investigate(capsys)
    assert "building synthetic datasets: C02" in out
    assert (tmp_path / "fresh" / "C02.db").exists()


# --- eval ----------------------------------------------------------------------


def test_eval_writes_json_and_markdown_results(env, capsys):
    out_dir = env / "eval"
    assert cli.main(["eval", "--cases", "C01", "C02", "--out", str(out_dir)]) == 0
    printed = capsys.readouterr().out
    assert "# OpsPilot evaluation" in printed and "results:" in printed
    assert "guardrail_detection_rate" not in printed
    files = sorted(p.suffix for p in out_dir.iterdir())
    assert files == [".json", ".md"]
    result = json.loads(next(out_dir.glob("*.json")).read_text())
    assert result["config"]["provider"] == "mock" and "guardrails" not in result


def test_eval_guardrails_reports_detection_rate(env, capsys):
    out_dir = env / "eval"
    assert cli.main(["eval", "--cases", "C01", "--guardrails", "--out", str(out_dir)]) == 0
    assert "guardrail_detection_rate:" in capsys.readouterr().out
    assert "guardrails" in json.loads(next(out_dir.glob("*.json")).read_text())


def test_eval_heldout_split(env, capsys):
    out_dir = env / "eval"
    assert cli.main(["eval", "--split", "heldout", "--out", str(out_dir)]) == 0
    assert "heldout split" in capsys.readouterr().out


# --- argument handling and settings ----------------------------------------------


def test_missing_command_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2 and "usage: opspilot" in capsys.readouterr().err


def test_investigate_requires_dataset_and_request(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["investigate", "--dataset", "C02"])
    assert exc.value.code == 2 and "--request" in capsys.readouterr().err


def test_live_flag_switches_provider_to_gemini(monkeypatch, env):
    seen = []

    async def fake_list(args, settings):
        seen.append(settings.provider)
        return 0

    monkeypatch.setattr(cli, "_list", fake_list)
    assert cli.main(["list"]) == 0
    assert cli.main(["--live", "list"]) == 0
    assert seen == ["mock", "gemini"]


# --- report rendering ------------------------------------------------------------


async def test_outcome_summary_shows_retried_rounds(run_case, settings, capsys):
    two_rounds = replace(settings, limits=replace(settings.limits, max_investigation_rounds=2))
    out = await run_case(
        "C11",
        settings_override=two_rounds,
        faults=RunFaults(model_timeout=frozenset({"incident_analyst"}), transient=True),
    )
    cli.print_outcome(out)
    assert "rounds: 2 (retried: incident_analyst)" in capsys.readouterr().out


async def test_outcome_summary_omits_rounds_for_single_pass(run_case, capsys):
    cli.print_outcome(await run_case("C02"))
    assert "rounds:" not in capsys.readouterr().out
