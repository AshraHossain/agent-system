"""Pytest configuration for agent-system tests."""

import os
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path so tests can import app/
sys.path.insert(0, str(Path(__file__).parent.parent))

# app.agents constructs its OpenAI client at import time. Tests never call
# the real API, so a placeholder key lets app.* modules import offline
# without requiring a real .env / OPENROUTER_API_KEY.
os.environ.setdefault("OPENROUTER_API_KEY", "test-placeholder-key")


@pytest.fixture(autouse=True)
def mock_route_step_to_tool(monkeypatch):
    """Auto-mock route_step_to_tool for all tests to prevent real LLM API calls.

    Tests can override this by providing their own monkeypatch or mock.
    Default behavior routes based on simple heuristics:
    - "calculator" if step contains arithmetic operators
    - "search" if step contains search keywords
    - "passthrough" otherwise
    """
    def default_router(step: str) -> str:
        step_lower = step.lower()
        if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
            return "calculator"
        if any(kw in step_lower for kw in ["search", "find", "look up", "query"]):
            return "search"
        return "passthrough"

    import app.graph as graph_module
    monkeypatch.setattr(graph_module, "route_step_to_tool", default_router)
