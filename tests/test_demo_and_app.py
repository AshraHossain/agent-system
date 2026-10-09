"""Phase 11: demo CLI and the ADK dev-tooling entry point."""

import importlib
import sys

from google.adk.apps import App

from opspilot import cli
from opspilot.adk.plugins import BudgetPlugin


def test_adk_app_entry_point(monkeypatch, data_dir):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(data_dir))
    sys.path.insert(0, "adk_apps")
    try:
        mod = importlib.import_module("opspilot_ai.agent")
        mod = importlib.reload(mod)
    finally:
        sys.path.remove("adk_apps")
    assert isinstance(mod.app, App) and mod.app.root_agent is mod.root_agent
    assert any(isinstance(p, BudgetPlugin) for p in mod.app.plugins)
    assert mod.root_agent.name == "opspilot_investigation"


def test_demo_cli_prints_all_sections(monkeypatch, data_dir, capsys):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(data_dir))
    assert cli.main(["demo", "--case", "C07", "--no-persist", "--show-labels"]) == 0
    out = capsys.readouterr().out
    for section in (
        "1. SYNTHETIC INCIDENT",
        "2. AGENT PIPELINE",
        "3. SPECIALIST FINDINGS",
        "4. EVIDENCE AND UNRESOLVED QUESTIONS",
        "5. VERIFICATION",
        "6. FINAL INVESTIGATION REPORT",
        "LABELS (shown after the run",
    ):
        assert section in out
    assert "status: requires_human_review" in out
    assert out.index("6. FINAL INVESTIGATION REPORT") < out.index("LABELS (shown after")


def test_demo_cli_json(monkeypatch, data_dir, capsys):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(data_dir))
    assert cli.main(["demo", "--case", "C01", "--no-persist", "--json"]) == 0
    assert '"status": "investigated"' in capsys.readouterr().out


def test_build_data_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPSPILOT_DATA_DIR", str(tmp_path))
    assert cli.main(["build-data", "C01"]) == 0
    assert (tmp_path / "C01.db").exists()
