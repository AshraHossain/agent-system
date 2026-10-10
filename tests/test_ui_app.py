"""Smoke test: the Streamlit page renders without an API connection."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "netpulse" / "ui" / "app.py")


def test_page_renders_with_synthetic_banner():
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    assert any("SYNTHETIC DATA" in w.value for w in at.warning)
