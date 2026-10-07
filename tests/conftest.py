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


def pytest_configure(config):
    """Configure pytest by patching route_step_to_tool before any tests run."""
    import app.agents
    import app.graph

    def mock_route_step_to_tool(step: str) -> str:
        """Mock router using heuristic routing without LLM calls."""
        step_lower = step.lower()
        if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
            return "calculator"
        if any(re.search(r"\b" + kw + r"\b", step_lower) for kw in ["search", "find", "look up", "query"]):
            return "search"
        return "passthrough"

    # Patch both the agents module and the graph module's imported reference
    app.agents.route_step_to_tool = mock_route_step_to_tool
    if hasattr(app.graph, 'route_step_to_tool'):
        app.graph.route_step_to_tool = mock_route_step_to_tool
