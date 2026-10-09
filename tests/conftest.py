"""Pytest configuration for NetPulse tests."""

import sys
from pathlib import Path

# Make the repo root (netpulse, synthgen, eval) and tests/ helpers importable.
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
