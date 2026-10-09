"""Node instrumentation: timing, trace records, timeouts and error capture.

Every node runs inside ``instrument``:

* a ``NodeTrace`` is appended for each execution;
* the node body runs under a time budget (tool or LLM budget from state). On
  timeout, ``on_timeout`` decides the update (e.g. "count a failed generation
  attempt"), otherwise the timeout is a recoverable error, or a fatal one for
  ``critical`` nodes;
* an expected ``ToolError`` becomes an ``ErrorRecord``. It is fatal only in
  ``critical`` nodes (intake, telemetry, detection, verification, ranking,
  report) and only when non-recoverable. Elsewhere the run continues degraded;
* any other exception is an internal error and is always fatal.

Limitation: Python threads cannot be killed, so a timed-out call keeps
running in its worker thread until it returns. Its result is discarded.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Literal

from langgraph.errors import GraphBubbleUp

from netpulse.errors import ToolError, ToolInputError, ToolTimeoutError
from netpulse.graph.deps import Deps
from netpulse.models import Budget, ErrorKind, ErrorRecord, NodeTrace

NodeFn = Callable[[dict, Deps], dict[str, Any]]


def error_record(node: str, kind: ErrorKind, exc: BaseException | str, recoverable: bool, deps: Deps) -> dict:
    message = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return ErrorRecord(
        node=node, kind=kind, message=message[:1000], recoverable=recoverable, at=deps.clock()
    ).model_dump(mode="json")


def _kind(exc: ToolError) -> ErrorKind:
    if isinstance(exc, ToolTimeoutError):
        return ErrorKind.TIMEOUT
    return ErrorKind.VALIDATION if isinstance(exc, ToolInputError) else ErrorKind.TOOL_FAILURE


def _run_with_timeout(fn: NodeFn, state: dict, deps: Deps, seconds: float | None) -> dict:
    if seconds is None:
        return fn(state, deps)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="netpulse-node")
    future = pool.submit(fn, state, deps)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout as exc:
        raise ToolTimeoutError(f"exceeded {seconds:g}s budget") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def instrument(
    name: str,
    fn: NodeFn,
    deps: Deps,
    *,
    timeout: Literal["tool", "llm"] | None = None,
    critical: bool = False,
    on_timeout: NodeFn | None = None,
) -> Callable[[dict], dict]:
    def node(state: dict) -> dict:
        started = deps.clock()
        t0 = time.perf_counter()
        seconds = None
        if timeout and state.get("budget"):
            b = Budget.model_validate(state["budget"])
            seconds = b.llm_timeout_seconds if timeout == "llm" else b.tool_timeout_seconds
        try:
            update = dict(_run_with_timeout(fn, state, deps, seconds) or {})
            outcome = "degraded" if update.get("errors") else "ok"
        except GraphBubbleUp:
            raise  # interrupt() and other LangGraph control flow must reach the runtime untouched
        except ToolTimeoutError as exc:
            update = dict(on_timeout(state, deps)) if on_timeout else {}
            update["errors"] = [error_record(name, ErrorKind.TIMEOUT, exc, not critical, deps)]
            update["fatal_error"] = critical
            outcome = "error"
        except ToolError as exc:
            # Expected tool errors only end the run in nodes the investigation cannot do without.
            fatal = critical and not exc.recoverable
            update = {"errors": [error_record(name, _kind(exc), exc, not fatal, deps)], "fatal_error": fatal}
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
