"""FastAPI query layer for the Traffic Flow Analyzer.

Exposes the core forecasting and briefing pipeline over HTTP so it can be
consumed by dashboards, other services, or CI smoke tests.

Start with::

    uv run trafficflow serve-api            # synthetic feed, port 8000
    uv run trafficflow serve-api --live     # MockLiveFeed (no API key needed)
    TOMTOM_API_KEY=... uv run trafficflow serve-api --live --provider tomtom

Or programmatically::

    import uvicorn
    from trafficflow.api import build_app
    from trafficflow.data.feed import SyntheticFeed

    feed = SyntheticFeed()
    uvicorn.run(build_app(feed), host="0.0.0.0", port=8000)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trafficflow.contracts import CongestionLevel
from trafficflow.data.feed import Feed

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel as _BaseModel
except ImportError as _err:
    raise ImportError("fastapi is required: pip install 'trafficflow[api]'") from _err


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SegmentInfoOut(_BaseModel):
    id: str
    name: str
    free_flow_kmh: float


class ForecastPointOut(_BaseModel):
    ts: datetime
    speed_kmh: float
    lower_kmh: float
    upper_kmh: float
    congestion_index: float
    level: str


class ForecastOut(_BaseModel):
    segment_id: str
    method: str
    horizon_minutes: int
    peak_index: float
    peak_level: str
    points: list[ForecastPointOut]


class HistoryOut(_BaseModel):
    segment_id: str
    days: float
    readings: list[dict[str, Any]]


class StatusOut(_BaseModel):
    as_of: datetime
    status: str
    segment_levels: dict[str, str]


class AskRequest(_BaseModel):
    question: str
    history: list[dict[str, str]] | None = None


class AskResponse(_BaseModel):
    answer: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(feed: Feed, model: str | None = None) -> FastAPI:
    """Return a FastAPI app backed by *feed*.

    All routes are read-only; the feed is never mutated by the API.
    """
    from trafficflow.core.anomaly import detect
    from trafficflow.core.congestion import congestion_index, level_for
    from trafficflow.core.forecast import forecast_segment
    from trafficflow.core.status import network_status

    app = FastAPI(
        title="Traffic Flow API",
        description="Read-only congestion forecasting endpoints.",
        version="0.1.0",
    )

    @app.get("/segments", response_model=list[SegmentInfoOut])
    def list_segments():
        return [
            SegmentInfoOut(id=s.id, name=s.name, free_flow_kmh=s.free_flow_kmh)
            for s in feed.segments()
        ]

    @app.get("/status", response_model=StatusOut)
    def get_status():
        segs = {s.id: s for s in feed.segments()}
        try:
            forecasts = [forecast_segment(feed.history(sid, 7), s, 60) for sid, s in segs.items()]
            anomalies = [a for sid in segs for a in detect(feed.history(sid, 7), sid)]
            ns = network_status(forecasts, anomalies)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        seg_levels: dict[str, str] = {}
        for sid, seg in segs.items():
            try:
                h = feed.history(sid, 0.1)
                speed = float(h.iloc[-1]) if not h.empty else seg.free_flow_kmh
                idx = congestion_index(speed, seg.free_flow_kmh)
                seg_levels[sid] = level_for(idx).value
            except Exception:
                seg_levels[sid] = CongestionLevel.FREE.value

        return StatusOut(as_of=feed.now, status=ns.value, segment_levels=seg_levels)

    @app.get("/forecast/{segment_id}", response_model=ForecastOut)
    def get_forecast(segment_id: str, horizon: int = 60):
        segs = {s.id: s for s in feed.segments()}
        if segment_id not in segs:
            raise HTTPException(status_code=404, detail=f"Segment {segment_id!r} not found")
        seg = segs[segment_id]
        try:
            fc = forecast_segment(feed.history(segment_id, 7), seg, horizon)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ForecastOut(
            segment_id=fc.segment_id,
            method=fc.method,
            horizon_minutes=fc.horizon_minutes,
            peak_index=fc.peak_index,
            peak_level=fc.peak_level.value,
            points=[
                ForecastPointOut(
                    ts=p.ts,
                    speed_kmh=p.speed_kmh,
                    lower_kmh=p.lower_kmh,
                    upper_kmh=p.upper_kmh,
                    congestion_index=p.congestion_index,
                    level=p.level.value,
                )
                for p in fc.points
            ],
        )

    @app.get("/history/{segment_id}", response_model=HistoryOut)
    def get_history(segment_id: str, days: float = 1.0):
        segs = {s.id for s in feed.segments()}
        if segment_id not in segs:
            raise HTTPException(status_code=404, detail=f"Segment {segment_id!r} not found")
        try:
            h = feed.history(segment_id, days)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        readings = [{"ts": str(ts), "speed_kmh": float(v)} for ts, v in h.items()]
        return HistoryOut(segment_id=segment_id, days=days, readings=readings)

    @app.post("/ask", response_model=AskResponse)
    async def ask_question(body: AskRequest):
        from trafficflow.agents.pipeline import ask

        try:
            answer = await ask(feed, body.question, model=model, history=body.history)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return AskResponse(answer=answer)

    return app
