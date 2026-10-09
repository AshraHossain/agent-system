"""Investigation service: start, inspect, approve and resume investigations on a checkpointed graph.

With a ``PersistentStore`` every super-step is checkpointed to SQLite, keyed by
``thread_id = incident_id``. A run paused at ``human_approval`` survives a
process restart: a new service on the same database can read the pending
review and resume it with ``Command(resume=...)``. Without a store an
in-memory saver is used. That is fine for tests and demos, but it is **not**
durable approval (``durable`` is False).
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from netpulse.errors import DataNotFoundError, ToolError, ToolInputError
from netpulse.graph import nodes
from netpulse.graph.builder import FAILURE, build_graph
from netpulse.graph.deps import Deps
from netpulse.graph.runner import initial_state
from netpulse.graph.wrapper import error_record
from netpulse.models import ROLE_RANK, Budget, ErrorKind, ReviewInput
from netpulse.persistence.store import PersistentStore


class AuthorizationError(ToolError):
    """The reviewer's role is insufficient for the pending decision."""

    recoverable = False


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    status: str
    approval_status: str | None
    pending_review: dict[str, Any] | None = None
    final_report: dict[str, Any] | None = None
    next_nodes: list[str] = []
    durable: bool


class InvestigationService:
    def __init__(self, deps: Deps, store: PersistentStore | None = None) -> None:
        self.deps = deps
        self.store = store
        self.durable = store is not None
        self.graph = build_graph(deps, checkpointer=store.checkpointer if store else InMemorySaver())

    # ------------------------------------------------------------ helpers

    def _config(self, incident_id: str, budget: Budget | None = None) -> dict:
        steps = (budget or Budget()).max_graph_steps
        return {"configurable": {"thread_id": incident_id}, "recursion_limit": steps}

    def _audit(self, incident_id: str, event: str, actor: str, payload: dict) -> None:
        if self.store:
            self.store.audit(incident_id, event, actor, payload, self.deps.clock())

    def _drive(self, incident_id: str, graph_input: Any) -> RunStatus:
        budget = (
            Budget.model_validate(self.state(incident_id).get("budget") or {})
            if self._exists(incident_id)
            else Budget.model_validate((graph_input or {}).get("budget") or {})
            if isinstance(graph_input, dict)
            else Budget()
        )
        config = self._config(incident_id, budget)
        try:
            for _ in self.graph.stream(graph_input, config=config, stream_mode="values"):
                pass
        except GraphRecursionError:
            values = self.graph.get_state(config).values
            err = error_record(
                "runner",
                ErrorKind.BUDGET_EXHAUSTED,
                f"graph step budget of {budget.max_graph_steps} exceeded",
                False,
                self.deps,
            )
            failed = {**values, "errors": [*(values.get("errors") or []), err], "fatal_error": True}
            update = {**nodes.failure_report(failed, self.deps), "errors": [err], "fatal_error": True}
            self.graph.update_state(config, update, as_node=FAILURE)
        result = self.status(incident_id)
        self._audit(
            incident_id,
            "run_paused" if result.pending_review else "run_finished",
            "system",
            {"status": result.status, "approval_status": result.approval_status},
        )
        return result

    def _exists(self, incident_id: str) -> bool:
        return bool(self.graph.get_state(self._config(incident_id)).values)

    # ------------------------------------------------------------- public

    def start(
        self,
        submission: dict[str, Any],
        *,
        submitted_at: str,
        incident_id: str,
        submitted_by: str = "local",
        source: Literal["api", "ui", "eval", "test"] = "api",
        budget: Budget | None = None,
    ) -> RunStatus:
        if self._exists(incident_id):
            raise ToolInputError(f"incident {incident_id!r} already exists")
        state = initial_state(
            submission,
            submitted_at=submitted_at,
            incident_id=incident_id,
            submitted_by=submitted_by,
            source=source,
            deps=self.deps,
            budget=budget,
        )
        self._audit(incident_id, "submitted", submitted_by, {"title": submission.get("title"), "source": source})
        return self._drive(incident_id, state)

    def state(self, incident_id: str) -> dict[str, Any]:
        values = self.graph.get_state(self._config(incident_id)).values
        if not values:
            raise DataNotFoundError(f"incident {incident_id!r} not found")
        return values

    def status(self, incident_id: str) -> RunStatus:
        snapshot = self.graph.get_state(self._config(incident_id))
        if not snapshot.values:
            raise DataNotFoundError(f"incident {incident_id!r} not found")
        interrupts = [i.value for task in snapshot.tasks for i in task.interrupts]
        values = snapshot.values
        return RunStatus(
            incident_id=incident_id,
            status=values.get("status", "running"),
            approval_status=values.get("approval_status"),
            pending_review=interrupts[0] if interrupts else None,
            final_report=values.get("final_report"),
            next_nodes=list(snapshot.next),
            durable=self.durable,
        )

    def decide(self, incident_id: str, review: ReviewInput) -> RunStatus:
        """Submit a reviewer decision. The caller must have authenticated ``review.reviewer_id``."""
        current = self.status(incident_id)
        if not current.pending_review:
            raise ToolInputError(f"incident {incident_id!r} is not awaiting approval")
        required = current.pending_review["required_role"]
        payload = review.model_dump(mode="json")
        if ROLE_RANK[review.reviewer_role] < ROLE_RANK[required]:
            self._audit(incident_id, "review_denied", review.reviewer_id, {**payload, "required_role": required})
            raise AuthorizationError(f"{required} role required; reviewer has {review.reviewer_role}")
        self._audit(incident_id, "review_submitted", review.reviewer_id, payload)
        return self._drive(incident_id, Command(resume=payload))

    def resume(self, incident_id: str) -> RunStatus:
        """Continue a run that stopped mid-graph (e.g. the process died). Paused reviews need ``decide``."""
        current = self.status(incident_id)
        if current.pending_review:
            raise ToolInputError("run is awaiting a reviewer decision; use decide()")
        if not current.next_nodes:
            return current
        self._audit(incident_id, "resumed", "system", {"next": current.next_nodes})
        return self._drive(incident_id, None)

    def audit_log(self, incident_id: str) -> list[dict[str, Any]]:
        return self.store.audit_log(incident_id) if self.store else []
