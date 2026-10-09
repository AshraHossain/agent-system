"""Stage order and the re-investigation round decision (ADR-0002 amendment).

Pure functions over session state, so the gate agent stays a thin wrapper and
the rules are unit-testable without ADK.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Stage outputs in pipeline order; specialists form one parallel group.
STAGE_GROUPS = [
    ["telemetry_finding", "topology_finding", "knowledge_finding"],
    ["incident_analysis"],
    ["report_draft"],
    ["verification"],
    ["review"],
]
# Groups produced inside the re-investigation loop (everything before review).
LOOP_GROUPS = STAGE_GROUPS[:4]
STAGE_AGENT = {
    "telemetry_finding": "telemetry_analyst",
    "topology_finding": "topology_analyst",
    "knowledge_finding": "knowledge_researcher",
    "incident_analysis": "incident_analyst",
    "report_draft": "report_drafter",
}
ROUNDS_KEY = "investigation_rounds"


@dataclass(frozen=True)
class RoundDecision:
    retry: list[str]
    state_delta: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def stop(self) -> bool:
        return not self.retry


def _failed(value) -> bool:
    return value is None or (isinstance(value, dict) and value.get("status") == "failed")


def _budget_failure(value) -> bool:
    """Budgets are per run, so a stage that ran out of one would fail the same way again."""
    errors = value.get("errors", []) if isinstance(value, dict) else []
    return any("budget" in str(e).lower() for e in errors)


def rounds_info(state: dict) -> dict:
    return state.get(ROUNDS_KEY) or {"round": 1, "retried": [], "stop_reason": ""}


def plan_next_round(state: dict, max_rounds: int) -> RoundDecision:
    """Decide, after verification, whether to re-run failed stages in another round.

    Only failed stages are actionable: the verifier's other evidence requests are
    for data that is missing at the source, which re-running cannot produce.
    Retried stages and everything downstream of them are cleared from state so
    their agents run again; completed upstream stages skip themselves.
    """
    info = rounds_info(state)
    current = info["round"]

    def stop(reason: str) -> RoundDecision:
        return RoundDecision(
            retry=[], state_delta={ROUNDS_KEY: {**info, "stop_reason": reason}}, reason=reason
        )

    if state.get("intake_error"):
        return stop("request rejected at intake")
    failed = [k for group in LOOP_GROUPS[:-1] for k in group if _failed(state.get(k))]
    retry = [k for k in failed if not _budget_failure(state.get(k))]
    if not failed:
        return stop("no failed stages")
    if not retry:
        return stop("failed stages ran out of budget; retrying cannot help")
    if current >= max_rounds:
        names = ", ".join(STAGE_AGENT[k] for k in retry)
        return stop(f"round limit ({max_rounds}) reached; still failed: {names}")

    first = next(i for i, group in enumerate(LOOP_GROUPS) if set(group) & set(retry))
    delta: dict = {k: None for k in LOOP_GROUPS[first] if k in retry}
    for group in LOOP_GROUPS[first + 1 :]:
        delta.update({k: None for k in group if state.get(k) is not None})
    names = [STAGE_AGENT[k] for k in retry]
    delta[ROUNDS_KEY] = {
        "round": current + 1,
        "retried": [*info["retried"], *names],
        "stop_reason": "",
    }
    return RoundDecision(
        retry=retry, state_delta=delta, reason=f"round {current + 1}: retrying {', '.join(names)}"
    )
