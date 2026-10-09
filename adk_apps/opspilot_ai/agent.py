"""Entry point for ADK's dev tooling:

    uv run adk web adk_apps          # dev UI on http://127.0.0.1:8000
    uv run adk api_server adk_apps   # REST API
    uv run adk run adk_apps/opspilot_ai

The dataset is chosen by OPSPILOT_DEFAULT_DATASET (default C02) unless a session
is created with `dataset_id` in its initial state. Datasets are built on first
import if missing.
"""

from google.adk.apps import App

from opspilot.adk.agents import TOOL_ALLOWLIST, build_root_agent
from opspilot.adk.plugins import BudgetPlugin
from opspilot.config import Settings
from opspilot.datasets.generate import build_all
from opspilot.observability import configure_logging

configure_logging()
_settings = Settings.from_env()
if not (_settings.data_dir / f"{_settings.default_dataset}.db").exists():
    build_all(_settings.data_dir)

root_agent = build_root_agent(_settings)
app = App(
    name="opspilot_ai",
    root_agent=root_agent,
    plugins=[BudgetPlugin(_settings.limits, TOOL_ALLOWLIST)],
)
