"""Pure figure/table builders (no Gradio), so they are unit-testable."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from trafficflow.contracts import CongestionLevel
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.core.forecast import forecast_segment
from trafficflow.data.feed import Feed

# Fixed approximate lat/lon for synthetic segments near Los Angeles
SEGMENT_COORDS: dict[str, tuple[float, float]] = {
    "S1": (34.05, -118.25),
    "S2": (34.06, -118.24),
    "S3": (34.07, -118.23),
    "S4": (34.05, -118.22),
    "S5": (34.04, -118.24),
    "S6": (34.06, -118.26),
}

_LEVEL_COLOR = {
    CongestionLevel.FREE: "green",
    CongestionLevel.MODERATE: "yellow",
    CongestionLevel.HEAVY: "orange",
    CongestionLevel.SEVERE: "red",
}

BAND = "rgba(37,99,235,0.18)"
LINE = "#2563eb"
FORECAST = "#d97706"

# Fixed approximate lat/lon for synthetic segments near Los Angeles
SEGMENT_COORDS: dict[str, tuple[float, float]] = {
    "S1": (34.05, -118.25),
    "S2": (34.06, -118.24),
    "S3": (34.07, -118.23),
    "S4": (34.05, -118.22),
    "S5": (34.04, -118.24),
    "S6": (34.06, -118.26),
}

_LEVEL_COLOR = {
    CongestionLevel.normal: "green",
    CongestionLevel.watch: "yellow",
    CongestionLevel.degraded: "orange",
    CongestionLevel.critical: "red",
}


def speed_figure(
    feed: Feed, segment_id: str, horizon_minutes: int, hours_back: int = 12
) -> go.Figure:
    seg = next(s for s in feed.segments() if s.id == segment_id)
    hist = feed.history(segment_id, days=7)
    fc = forecast_segment(hist, seg, horizon_minutes)
    recent = hist.iloc[-hours_back * 12 :]
    ts = [p.ts for p in fc.points]
    fig = go.Figure()
    fig.add_scatter(
        mode="lines", x=recent.index, y=recent.values, name="observed", line={"color": LINE}
    )
    fig.add_scatter(
        mode="lines",
        x=ts,
        y=[p.upper_kmh for p in fc.points],
        line={"width": 0},
        showlegend=False,
        hoverinfo="skip",
    )
    fig.add_scatter(
        mode="lines",
        x=ts,
        y=[p.lower_kmh for p in fc.points],
        fill="tonexty",
        fillcolor=BAND,
        line={"width": 0},
        name="90% interval",
        hoverinfo="skip",
    )
    fig.add_scatter(
        mode="lines",
        x=ts,
        y=[p.speed_kmh for p in fc.points],
        name="forecast",
        line={"color": FORECAST, "dash": "dash"},
    )
    fig.add_hline(
        y=seg.free_flow_kmh, line_dash="dot", line_color="#6b7280", annotation_text="free flow"
    )
    fig.update_layout(
        title=f"{seg.name} ({seg.id}): speed and forecast",
        yaxis_title="km/h",
        margin={"l": 40, "r": 10, "t": 50, "b": 30},
        legend={"orientation": "h", "y": -0.2},
        template="plotly_white",
    )
    return fig


def heatmap_figure(feed: Feed, horizon_minutes: int, hours_back: int = 3) -> go.Figure:
    """Segments x time congestion index; observed on the left, forecast on the right."""
    segs = feed.segments()
    rows, ylabels = [], []
    for seg in segs:
        hist = feed.history(seg.id, days=7)
        obs = hist.iloc[-hours_back * 12 :]
        fc = forecast_segment(hist, seg, horizon_minutes)
        idx = [congestion_index(v, seg.free_flow_kmh) for v in obs.values]
        idx += [p.congestion_index for p in fc.points]
        times = list(obs.index) + [p.ts for p in fc.points]
        rows.append(pd.Series(idx, index=times))
        ylabels.append(f"{seg.id} {seg.name}")
    df = pd.DataFrame(rows, index=ylabels)
    fig = go.Figure(
        go.Heatmap(
            z=df.values,
            x=df.columns,
            y=df.index,
            zmin=0,
            zmax=1,
            colorscale="YlOrRd",
            colorbar={"title": "congestion"},
        )
    )
    fig.add_vline(
        x=feed.now.timestamp() * 1000, line_dash="dash", line_color="#111827", annotation_text="now"
    )
    fig.update_layout(
        title="Congestion index: observed (left) and forecast (right)",
        margin={"l": 10, "r": 10, "t": 50, "b": 30},
        template="plotly_white",
        yaxis={"autorange": "reversed"},
    )
    return fig


def map_figure(
    points: list[tuple[str, float, float, CongestionLevel]],
) -> go.Figure:
    """Scatter map colored by congestion level.

    `points` is a list of (segment_id, lat, lon, level).
    """
    df = pd.DataFrame(points, columns=["segment_id", "lat", "lon", "level"])
    df["color"] = df["level"].map(_LEVEL_COLOR)
    df["level_str"] = df["level"].map(lambda lv: lv.value)
    fig = px.scatter_mapbox(
        df,
        lat="lat",
        lon="lon",
        color="level_str",
        color_discrete_map={lv.value: c for lv, c in _LEVEL_COLOR.items()},
        hover_name="segment_id",
        hover_data={"lat": False, "lon": False, "level_str": True},
        zoom=11,
        height=400,
    )
    fig.update_layout(
        mapbox_style="open-street-map",
        margin={"l": 0, "r": 0, "t": 30, "b": 0},
        title="Segment congestion map",
        legend_title="Level",
    )
    return fig


def status_table(feed: Feed, horizon_minutes: int) -> pd.DataFrame:
    out = []
    for seg in feed.segments():
        hist = feed.history(seg.id, days=7)
        speed = float(hist.iloc[-3:].mean())
        now_idx = congestion_index(speed, seg.free_flow_kmh)
        fc = forecast_segment(hist, seg, horizon_minutes)
        out.append(
            {
                "segment": f"{seg.id} {seg.name}",
                "speed km/h": round(speed, 1),
                "now": level_for(now_idx).value,
                f"peak next {fc.horizon_minutes} min": fc.peak_level.value,
                "peak at": fc.peak_ts.strftime("%H:%M"),
            }
        )
    return pd.DataFrame(out)
