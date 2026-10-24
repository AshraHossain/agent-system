# ADR-0002: Fixed pipeline instead of LLM-driven delegation

**Status:** Accepted

## Context
PydanticAI supports agent delegation (an agent calling other agents as tools) and graph
workflows, in addition to plain Python orchestration.

## Decision
`pipeline.run_report` runs the Forecast and Incident analysts concurrently with
`asyncio.gather`, then the Brief Writer, then deterministic verification and status.

## Alternatives considered
* **Coordinator agent delegating to analysts** — rejected: routing becomes nondeterministic,
  "did every analyst run exactly once?" becomes untestable, and cost is harder to bound.
* **Graph workflow** — no conditional routing is needed.

## Consequences
Stable traces and a simple termination condition. Adding re-investigation on failed
verification would be an explicit, bounded loop, not model-driven.
