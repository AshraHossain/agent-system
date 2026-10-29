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

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from trafficflow.contracts import CongestionLevel
from trafficflow.data.feed import Feed

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel as _BaseModel
except ImportError as _err:
    raise ImportError("fastapi is required: pip install 'trafficflow[api]'") from _err

# Same cap as the agents' forecast tools; work and response size grow with the horizon.
MAX_HORIZON_MINUTES = 180


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


class Turn(_BaseModel):
    role: str
    content: str


class AskRequest(_BaseModel):
    question: str
    history: list[Turn] | None = None


class AskResponse(_BaseModel):
    answer: str


class PathOut(_BaseModel):
    source: str
    target: str
    path: list[str]
    total_km: float
    travel_minutes: float
    bottleneck: str


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
    from trafficflow.core.freshness import stale_segments
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
            hists = {sid: feed.history(sid, 7) for sid in segs}
            forecasts = [forecast_segment(hists[sid], s, 60) for sid, s in segs.items()]
            anomalies = [
                a
                for sid, s in segs.items()
                for a in detect(hists[sid], sid, free_flow_kmh=s.free_flow_kmh)
            ]
            ns = network_status(forecasts, anomalies, stale_segments(hists, feed.now))
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
    def get_forecast(segment_id: str, horizon: int = Query(60, le=MAX_HORIZON_MINUTES)):
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

    @app.get("/path/{source}/{target}", response_model=PathOut)
    def get_path(source: str, target: str, weight: str = "time"):
        """Shortest path between two segments (by travel time or distance)."""
        from trafficflow.core.graph import SegmentGraph

        valid_ids = {s.id for s in feed.segments()}
        if source not in valid_ids:
            raise HTTPException(status_code=404, detail=f"Segment {source!r} not found")
        if target not in valid_ids:
            raise HTTPException(status_code=404, detail=f"Segment {target!r} not found")
        g = SegmentGraph()
        g.update_speeds(feed)
        result = g.shortest_path(source, target, weight=weight)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No path from {source} to {target}")
        return PathOut(
            source=source,
            target=target,
            path=result.path,
            total_km=result.total_km,
            travel_minutes=result.travel_minutes,
            bottleneck=result.bottleneck,
        )

    @app.post("/ask", response_model=AskResponse)
    async def ask_question(body: AskRequest):
        from trafficflow.agents.pipeline import ask

        try:
            history = [t.model_dump() for t in body.history] if body.history else None
            answer = await ask(feed, body.question, model=model, history=history)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return AskResponse(answer=answer)

    @app.get(
        "/stream",
        summary="SSE: live speed updates for all segments",
        response_class=StreamingResponse,
    )
    async def stream_speeds(interval: float = 5.0):
        """Server-Sent Events endpoint.

        Pushes one JSON event per *interval* seconds with current speeds and
        congestion levels for every segment.  Connect with::

            curl -N http://localhost:8000/stream

        Each event is ``data: <json>\\n\\n`` following the SSE spec.
        """
        interval = max(1.0, min(interval, 60.0))

        async def _generate() -> AsyncIterator[str]:
            from trafficflow.core.congestion import congestion_index, level_for

            while True:
                readings: dict[str, Any] = {"ts": datetime.now().isoformat(), "segments": {}}
                for seg in feed.segments():
                    try:
                        h = feed.history(seg.id, days=0.02)
                        speed = float(h.iloc[-1]) if not h.empty else seg.free_flow_kmh
                    except Exception:
                        speed = seg.free_flow_kmh
                    idx = congestion_index(speed, seg.free_flow_kmh)
                    readings["segments"][seg.id] = {
                        "speed_kmh": round(speed, 2),
                        "congestion_index": round(idx, 3),
                        "level": level_for(idx).value,
                    }
                yield f"data: {json.dumps(readings)}\n\n"
                await asyncio.sleep(interval)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app
