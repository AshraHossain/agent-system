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

# Mark that we're in pytest so app modules can detect test mode
os.environ["PYTEST_CURRENT_TEST"] = "conftest:setup"
