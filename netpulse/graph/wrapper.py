"""Node instrumentation: timing, trace records, and error capture.

Every node runs inside ``instrument``:

* a ``NodeTrace`` is appended for each execution;
* an expected ``ToolError`` becomes an ``ErrorRecord``. Recoverable errors
  let the workflow continue in a degraded state; non-recoverable ones set
  ``fatal_error``;
* any other exception is an internal error and is always fatal.

Routers send ``fatal_error`` to ``failure_report``, so an incident is never
silently lost.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from netpulse.errors import ToolError, ToolInputError
from netpulse.graph.deps import Deps
from netpulse.models import ErrorKind, ErrorRecord, NodeTrace

NodeFn = Callable[[dict, Deps], dict[str, Any]]


def error_record(node: str, kind: ErrorKind, exc: BaseException | str, recoverable: bool, deps: Deps) -> dict:
    message = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return ErrorRecord(
        node=node, kind=kind, message=message[:1000], recoverable=recoverable, at=deps.clock()
    ).model_dump(mode="json")


def _kind(exc: ToolError) -> ErrorKind:
    return ErrorKind.VALIDATION if isinstance(exc, ToolInputError) else ErrorKind.TOOL_FAILURE


def instrument(name: str, fn: NodeFn, deps: Deps) -> Callable[[dict], dict]:
    def node(state: dict) -> dict:
        started = deps.clock()
        t0 = time.perf_counter()
        try:
            update = dict(fn(state, deps) or {})
            outcome = "degraded" if update.get("errors") else "ok"
        except ToolError as exc:
            update = {
                "errors": [error_record(name, _kind(exc), exc, exc.recoverable, deps)],
                "fatal_error": not exc.recoverable,
            }
            outcome = "error"
        except Exception as exc:  # noqa: BLE001 - convert to a structured failure, never lose the incident
            update = {"errors": [error_record(name, ErrorKind.INTERNAL, exc, False, deps)], "fatal_error": True}
            outcome = "error"
        trace = NodeTrace(
            node=name,
            started_at=started,
            duration_ms=round((time.perf_counter() - t0) * 1000, 3),
            outcome=outcome,
            detail=update.pop("_trace_detail", ""),
        )
        update["node_trace"] = [trace.model_dump(mode="json")]
        return update

    node.__name__ = name
    return node
