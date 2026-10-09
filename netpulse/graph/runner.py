"""Run one investigation end to end, with the graph step budget enforced.

LangGraph raises ``GraphRecursionError`` when ``recursion_limit`` is hit.
That aborts ``invoke`` and discards the in-flight state. We therefore stream
state snapshots and, on that error, build a structured failure report from
the last snapshot, so the incident is not lost.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from langgraph.errors import GraphRecursionError

from netpulse.graph import nodes
from netpulse.graph.builder import build_graph
from netpulse.graph.deps import Deps
from netpulse.graph.wrapper import error_record
from netpulse.models import Budget, ErrorKind


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
    last = dict(state)
    try:
        for snapshot in graph.stream(state, config={"recursion_limit": max_steps}, stream_mode="values"):
            last = snapshot
    except GraphRecursionError:
        err = error_record(
            "runner", ErrorKind.BUDGET_EXHAUSTED, f"graph step budget of {max_steps} exceeded", False, deps
        )
        last = {**last, "errors": [*(last.get("errors") or []), err], "fatal_error": True}
        last.update(nodes.failure_report(last, deps))
    return last
