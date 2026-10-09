"""Pytest configuration for agent-system tests."""

import os
import re
import sys
from pathlib import Path

import pytest

# Add parent directory to path so tests can import app/
sys.path.insert(0, str(Path(__file__).parent.parent))

# app.agents constructs its OpenAI client at import time. Tests never call
# the real API, so a placeholder key lets app.* modules import offline
# without requiring a real .env / OPENROUTER_API_KEY.
os.environ.setdefault("OPENROUTER_API_KEY", "test-placeholder-key")


def _fake_route_step_to_tool(step: str) -> str:
    if any(op in step for op in ["+", "-", "*", "/", "%"]):
        return "calculator"
    if any(re.search(rf"\b{kw}\b", step.lower()) for kw in ["search", "find", "look up", "query"]):
        return "search"
    return "passthrough"


@pytest.fixture(autouse=True)
def fake_router(monkeypatch):
    # Patch the name app.graph looks up at call time; app.agents' copy is never called.
    monkeypatch.setattr("app.graph.route_step_to_tool", _fake_route_step_to_tool)
