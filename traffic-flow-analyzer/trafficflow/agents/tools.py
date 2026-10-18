"""Read-only, validated tools. They never accept paths, SQL, URLs or commands."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from trafficflow.agents.deps import TrafficDeps
from trafficflow.core.anomaly import detect
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.core.forecast import forecast_moving_average as _forecast_ma
from trafficflow.core.forecast import forecast_persistence as _forecast_persistence
from trafficflow.core.forecast import forecast_segment as _forecast

Ctx = RunContext[TrafficDeps]


def _segment(ctx: Ctx, segment_id: str):
    for s in ctx.deps.feed.segments():
        if s.id == segment_id:
            return s
    return None


def _unknown(ctx: Ctx, segment_id: str) -> dict[str, Any]:
    known = [s.id for s in ctx.deps.feed.segments()]
    return {"status": "error", "error": f"unknown segment_id {segment_id!r}", "known": known}


async def list_segments(ctx: Ctx) -> dict[str, Any]:
    """List monitored road segments with their free-flow speed."""
    segs = [s.model_dump() for s in ctx.deps.feed.segments()]
    return {"status": "ok", "now": ctx.deps.feed.now.isoformat(), "segments": segs}


async def get_current_conditions(ctx: Ctx, segment_id: str) -> dict[str, Any]:
    """Latest speed and congestion level for one segment."""
    seg = _segment(ctx, segment_id)
    if seg is None:
        return _unknown(ctx, segment_id)
    hist = ctx.deps.feed.history(segment_id, days=1)
    speed = float(hist.iloc[-3:].mean())
    idx = congestion_index(speed, seg.free_flow_kmh)
    data = {
        "segment_id": segment_id,
        "as_of": hist.index[-1].isoformat(),
        "speed_kmh": round(speed, 1),
        "congestion_index": round(idx, 3),
        "level": level_for(idx).value,
    }
    return {"status": "ok", "evidence_id": ctx.deps.record("current", data), **data}


async def forecast_segment(
    ctx: Ctx,
    segment_id: str,
    horizon_minutes: Annotated[int, Field(ge=5, le=180)] = 60,
) -> dict[str, Any]:
    """Forecast speed and congestion for one segment (deterministic model, 90% interval)."""
    seg = _segment(ctx, segment_id)
    if seg is None:
        return _unknown(ctx, segment_id)
    try:
        fc = _forecast(ctx.deps.feed.history(segment_id, days=7), seg, horizon_minutes)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    data = {
        "segment_id": segment_id,
        "method": fc.method,
        "horizon_minutes": fc.horizon_minutes,
        "peak_congestion_index": fc.peak_index,
        "peak_level": fc.peak_level.value,
        "peak_at": fc.peak_ts.isoformat(),
        "speed_at_horizon_kmh": fc.points[-1].speed_kmh,
    }
    return {"status": "ok", "evidence_id": ctx.deps.record("forecast", data), **data}


async def compare_models(
    ctx: Ctx,
    segment_id: str,
    horizon_minutes: Annotated[int, Field(ge=5, le=180)] = 60,
) -> dict[str, Any]:
    """Run three forecasters on one segment and return the first 3 points from each."""
    seg = _segment(ctx, segment_id)
    if seg is None:
        return _unknown(ctx, segment_id)
    hist = ctx.deps.feed.history(segment_id, days=7)
    results: dict[str, Any] = {}
    for name, fn in (
        ("seasonal_residual", _forecast),
        ("moving_average", _forecast_ma),
        ("persistence", _forecast_persistence),
    ):
        try:
            fc = fn(hist, seg, horizon_minutes)
            results[name] = [p.model_dump(mode="json") for p in fc.points[:3]]
        except ValueError as exc:
            results[name] = {"error": str(exc)}
    data = {
        "segment_id": segment_id,
        "horizon_minutes": horizon_minutes,
        "models": results,
    }
    return {"status": "ok", "evidence_id": ctx.deps.record("compare_models", data), **data}


async def detect_incidents(ctx: Ctx, segment_id: str) -> dict[str, Any]:
    """Find sustained abnormal slowdowns on one segment in the last two hours."""
    if _segment(ctx, segment_id) is None:
        return _unknown(ctx, segment_id)
    found = detect(ctx.deps.feed.history(segment_id, days=7), segment_id, min_drop_kmh=5.0)
    data = {
        "segment_id": segment_id,
        "anomalies": [a.model_dump(mode="json") for a in found],
    }
    return {"status": "ok", "evidence_id": ctx.deps.record("anomaly_scan", data), **data}
