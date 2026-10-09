"""Convenience entry point: run one investigation end to end."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.models import Budget


def initial_state(
    submission: dict[str, Any],
    *,
    submitted_at: datetime | str,
    incident_id: str | None = None,
    submitted_by: str = "local",
    source: str = "api",
    deps: Deps,
    budget: Budget | None = None,
) -> dict[str, Any]:
    return {
        "incident_id": incident_id or f"inc-{uuid.uuid4().hex[:12]}",
        "submission": submission,
        "request_metadata": {
            "execution_id": f"exec-{uuid.uuid4().hex[:12]}",
            "submitted_by": submitted_by,
            "submitted_at": submitted_at if isinstance(submitted_at, str) else submitted_at.isoformat(),
            "source": source,
            "llm_provider": deps.generator.provider,
            "llm_model": deps.generator.model,
        },
        "budget": (budget or Budget()).model_dump(mode="json"),
    }


def run_investigation(deps: Deps, state: dict[str, Any], graph=None) -> dict[str, Any]:
    graph = graph or build_graph(deps)
    max_steps = Budget.model_validate(state.get("budget") or {}).max_graph_steps
    return graph.invoke(state, config={"recursion_limit": max_steps})
