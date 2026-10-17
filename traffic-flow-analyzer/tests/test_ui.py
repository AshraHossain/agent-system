import socket

import plotly.graph_objects as go
import pytest

from trafficflow.data.feed import SyntheticFeed
from trafficflow.ui.app import build_app
from trafficflow.ui.plots import heatmap_figure, speed_figure, status_table

_CHROMIUM = "/opt/pw-browsers/chromium-1161/chrome-linux/chrome"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_dashboard_browser_smoke():
    """Launch the Gradio app and verify the page loads in a headless browser."""
    pytest.importorskip("playwright", reason="playwright not installed")
    from playwright.sync_api import sync_playwright

    app = build_app()
    port = _free_port()
    app.launch(server_port=port, prevent_thread_lock=True, show_error=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=_CHROMIUM,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}", timeout=30000)
            page.wait_for_selector("gradio-app", timeout=20000)
            browser.close()
    finally:
        app.close()


def test_figures_and_table():
    feed = SyntheticFeed()
    assert isinstance(speed_figure(feed, "S1", 60), go.Figure)
    assert isinstance(heatmap_figure(feed, 60), go.Figure)
    t = status_table(feed, 60)
    assert len(t) == 6 and "segment" in t.columns


def test_app_builds():
    assert build_app() is not None


def test_cli_demo(capsys):
    from trafficflow.cli import main

    assert main(["demo"]) == 0
    assert "DEGRADED" in capsys.readouterr().out
