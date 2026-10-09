# ADR-0002: Fixed workflow tree instead of LLM-driven delegation

**Status:** Accepted

## Context
ADK supports (a) an `LlmAgent` coordinator with `sub_agents`, where the model
transfers control; (b) workflow agents (`SequentialAgent`, `ParallelAgent`,
`LoopAgent`); (c) in 2.x, a graph `Workflow` with nodes and edges.

## Decision
Root `SequentialAgent` → deterministic `intake` → `ParallelAgent` of three
independent specialists → `incident_analyst` → `report_drafter` →
deterministic `evidence_verifier` → `review_verifier` → deterministic
`finalizer`. Specialists set `disallow_transfer_to_parent/peers=True` and have
no `sub_agents`, so no agent can delegate.

## Alternatives considered
* **LLM coordinator with transfer** — rejected: nondeterministic routing, can
  skip or repeat specialists, recursion risk, harder to test.
* **`LoopAgent` re-investigation (max 2 rounds) when verification fails** —
  deferred: doubles cost on hard cases; status `inconclusive` plus explicit
  `additional_evidence_requests` is more honest for v1. If added, use
  `LoopAgent(max_iterations=2)` with the verifier escalating to exit.
* **2.x graph `Workflow`** — viable, but no conditional routing is needed.

## Consequences
Every run executes every stage exactly once → termination is structural,
traces are stable, and orchestration tests can assert exact agent order.
