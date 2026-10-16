"""Deterministic offline models (FunctionModel policies) for tests, demos and CI.
They drive the real tools and produce schema-valid outputs; they are not an LLM."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


def _returns(messages: list[ModelMessage], tool: str) -> list[dict]:
    out = []
    for m in messages:
        for p in getattr(m, "parts", []):
            if (
                isinstance(p, ToolReturnPart)
                and p.tool_name == tool
                and isinstance(p.content, dict)
            ):
                out.append(p.content)
    return out


def _final(info: AgentInfo, payload: dict[str, Any]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])


def _calls(tool: str, args: list[dict]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, a) for a in args])


def _prompt_text(messages: list[ModelMessage]) -> str:
    for m in messages:
        for p in getattr(m, "parts", []):
            if getattr(p, "part_kind", "") == "user-prompt":
                return str(p.content)
    return ""


async def _forecast_policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    segs = _returns(messages, "list_segments")
    if not segs:
        return _calls("list_segments", [{}])
    ids = [s["id"] for s in segs[0]["segments"]]
    fcs = _returns(messages, "forecast_segment")
    if not fcs:
        horizon = (
            int(_prompt_text(messages).split("horizon=")[-1].split()[0] or 60)
            if "horizon=" in _prompt_text(messages)
            else 60
        )
        return _calls(
            "forecast_segment", [{"segment_id": i, "horizon_minutes": horizon} for i in ids]
        )
    outlooks = [
        {
            "segment_id": f["segment_id"],
            "note": f"Peak {f['peak_level']} (index {f['peak_congestion_index']}) around "
            f"{f['peak_at'][11:16]}; {f['speed_at_horizon_kmh']} km/h at horizon.",
            "evidence_ids": [f["evidence_id"]],
        }
        for f in fcs
        if f.get("status") == "ok"
    ]
    return _final(info, {"outlooks": outlooks})


async def _incident_policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    segs = _returns(messages, "list_segments")
    if not segs:
        return _calls("list_segments", [{}])
    scans = _returns(messages, "detect_incidents")
    if not scans:
        return _calls("detect_incidents", [{"segment_id": s["id"]} for s in segs[0]["segments"]])
    notes = []
    for s in scans:
        for a in s.get("anomalies", []):
            notes.append(
                {
                    "segment_id": s["segment_id"],
                    "summary": f"Sustained slowdown since {a['start'][11:16]} "
                    f"(min {a['min_speed_kmh']} km/h, z={a['robust_z']}).",
                    "evidence_ids": [s["evidence_id"]],
                }
            )
    return _final(info, {"incidents": notes})


async def _writer_policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    data = json.loads(_prompt_text(messages).split("FINDINGS:", 1)[1])
    findings, recs = [], []
    for inc in data["incidents"]["incidents"]:
        findings.append(
            {
                "claim": inc["summary"],
                "segment_ids": [inc["segment_id"]],
                "evidence_ids": inc["evidence_ids"],
            }
        )
        recs.append(f"Monitor {inc['segment_id']} and notify drivers of the slowdown.")
    for o in data["forecast"]["outlooks"]:
        if "heavy" in o["note"] or "severe" in o["note"]:
            findings.append(
                {
                    "claim": o["note"],
                    "segment_ids": [o["segment_id"]],
                    "evidence_ids": o["evidence_ids"],
                }
            )
    if not findings and data["forecast"]["outlooks"]:
        o = data["forecast"]["outlooks"][0]
        findings.append(
            {
                "claim": "No heavy congestion forecast on any segment.",
                "segment_ids": [],
                "evidence_ids": o["evidence_ids"],
            }
        )
    head = f"{len(data['incidents']['incidents'])} incident(s), {len(findings)} notable finding(s)"
    return _final(
        info,
        {
            "headline": head,
            "summary": "; ".join(f["claim"] for f in findings)[:1100],
            "findings": findings,
            "recommendations": recs[:6],
        },
    )


async def _ask_policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    q = _prompt_text(messages).lower()
    segs = _returns(messages, "list_segments")
    if not segs:
        return _calls("list_segments", [{}])
    ids = [s["id"] for s in segs[0]["segments"] if s["id"].lower() in q or s["name"].lower() in q]
    fcs = _returns(messages, "forecast_segment")
    if not fcs:
        return _calls(
            "forecast_segment",
            [
                {"segment_id": i, "horizon_minutes": 60}
                for i in (ids or [segs[0]["segments"][0]["id"]])
            ],
        )
    f = fcs[0]
    return ModelResponse(
        parts=[
            TextPart(
                f"{f['segment_id']}: peak {f['peak_level']} (index {f['peak_congestion_index']}) "
                f"expected around {f['peak_at'][11:16]}; {f['speed_at_horizon_kmh']} km/h at +"
                f"{f['horizon_minutes']} min [{f['evidence_id']}]."
            )
        ]
    )


def mock_model(role: str) -> FunctionModel:
    policy = {
        "forecast": _forecast_policy,
        "incident": _incident_policy,
        "writer": _writer_policy,
        "ask": _ask_policy,
    }[role]
    return FunctionModel(policy, model_name=f"mock-{role}")
