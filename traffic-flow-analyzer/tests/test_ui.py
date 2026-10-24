import os
import socket

import plotly.graph_objects as go
import pytest

from trafficflow.data.feed import SyntheticFeed
from trafficflow.ui.app import build_app
from trafficflow.ui.plots import heatmap_figure, speed_figure, status_table

# Pre-installed browser in managed sandboxes; CI uses `playwright install chromium` instead.
_CHROMIUM = next(
    (
        p
        for p in ("/opt/pw-browsers/chromium", "/opt/pw-browsers/chromium-1161/chrome-linux/chrome")
        if os.path.exists(p)
    ),
    None,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_dashboard_browser_smoke():
    """Launch the dashboard in a headless browser and drive the briefing and Ask flows."""
    pytest.importorskip("playwright", reason="playwright not installed")
    from playwright.sync_api import sync_playwright

    app = build_app()
    port = _free_port()
    app.launch(server_port=port, prevent_thread_lock=True, show_error=True)
    try:
        with sync_playwright() as p:
            launch_kwargs = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
            if _CHROMIUM:
                launch_kwargs["executable_path"] = _CHROMIUM
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}", timeout=30000)
            page.wait_for_selector("gradio-app", timeout=20000)

            page.get_by_role("button", name="Generate briefing").click()
            page.get_by_text("rule-based status").wait_for(timeout=60000)

            page.get_by_role("tab", name="Ask").click()
            page.get_by_label("Question").fill("How is S1 in the next hour?")
            page.get_by_role("button", name="Ask", exact=True).click()
            page.get_by_text("You:").wait_for(timeout=60000)

            page.get_by_role("tab", name="History").click()
            page.get_by_label("Past reports").click()
            assert page.get_by_role("option").count() >= 1
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
