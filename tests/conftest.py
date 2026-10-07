"""Pytest configuration for agent-system tests."""

import os
import sys
from pathlib import Path

# Add parent directory to path so tests can import app/
sys.path.insert(0, str(Path(__file__).parent.parent))

# app.agents constructs its OpenAI client at import time. Tests never call
# the real API, so a placeholder key lets app.* modules import offline
# without requiring a real .env / OPENROUTER_API_KEY.
os.environ.setdefault("OPENROUTER_API_KEY", "test-placeholder-key")

# Define mock router before importing any app modules
def _mock_route_step_to_tool(step: str) -> str:
    """Mock router for tests - routes based on simple heuristics.

    Prevents real LLM API calls during test execution.
    """
    step_lower = step.lower()
    if any(op in step for op in ["+", "-", "*", "/", "%", "**"]):
        return "calculator"
    if any(kw in step_lower for kw in ["search", "find", "look up", "query"]):
        return "search"
    return "passthrough"

# Import and patch app.agents.route_step_to_tool before graph imports it
import app.agents
app.agents.route_step_to_tool = _mock_route_step_to_tool
