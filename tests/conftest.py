"""Pytest configuration for agent-system tests."""

import pytest
import sys
from pathlib import Path

# Add parent directory to path so tests can import app/
sys.path.insert(0, str(Path(__file__).parent.parent))
