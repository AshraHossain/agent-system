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


def mock_route_step_to_tool(step: str) -> str:
    """Mock router using heuristic routing without LLM calls."""
    step_lower = step.lower()
    if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
        return "calculator"
    if any(re.search(r"\b" + kw + r"\b", step_lower) for kw in ["search", "find", "look up", "query"]):
        return "search"
    return "passthrough"


@pytest.fixture(scope="session", autouse=True)
def _patch_route_step_to_tool():
    """Autouse session fixture to patch route_step_to_tool before any tests run."""
    import app.agents
    app.agents.route_step_to_tool = mock_route_step_to_tool
