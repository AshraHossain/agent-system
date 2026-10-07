"""Pytest configuration for agent-system tests."""

import os
import re
import sys
from pathlib import Path

# Add parent directory to path so tests can import app/
sys.path.insert(0, str(Path(__file__).parent.parent))

# app.agents constructs its OpenAI client at import time. Tests never call
# the real API, so a placeholder key lets app.* modules import offline
# without requiring a real .env / OPENROUTER_API_KEY.
os.environ.setdefault("OPENROUTER_API_KEY", "test-placeholder-key")


def _mock_route_step_to_tool(step: str) -> str:
    """Mock router using heuristic routing without LLM calls."""
    step_lower = step.lower()
    if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
        return "calculator"
    if any(re.search(r"\b" + kw + r"\b", step_lower) for kw in ["search", "find", "look up", "query"]):
        return "search"
    return "passthrough"


# Patch app.agents.route_step_to_tool at conftest load time, BEFORE test modules
# are imported. This ensures app/graph will capture the mocked version.
import app.agents
app.agents.route_step_to_tool = _mock_route_step_to_tool
