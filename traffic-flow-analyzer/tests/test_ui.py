import plotly.graph_objects as go

from trafficflow.data.feed import SyntheticFeed
from trafficflow.ui.app import build_app
from trafficflow.ui.plots import heatmap_figure, speed_figure, status_table


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
