# ADR-0002: Fixed workflow tree instead of LLM-driven delegation

**Status:** Accepted; amended (optional re-investigation loop, below)

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

## Amendment: optional re-investigation loop

`OPSPILOT_MAX_INVESTIGATION_ROUNDS=2` (default `1`) wraps specialists →
`incident_analyst` → `report_drafter` → `evidence_verifier` in
`LoopAgent(max_iterations=2)`, followed by a deterministic
`reinvestigation_gate`. With the default, the tree above is unchanged.

* **Trigger: failed stages only.** The verifier's other evidence requests ask
  for data missing at the source, which re-running cannot produce. Stages that
  failed because a budget ran out are not retried either, since budgets are per
  run.
* **Mechanism.** The gate clears the failed stages and everything downstream
  from state; completed upstream stages skip themselves (`skip_if_done`), so
  they cost no model calls in the second round. Otherwise it emits
  `escalate`, which ends the `LoopAgent`; `SequentialAgent` ignores
  `escalate`, so review and the finalizer still run once.
* **Deviation from the original sketch.** The decision lives in a separate gate
  agent (`opspilot/adk/rounds.py`, pure and unit-tested) rather than in the
  verifier, so the verifier stays identical with and without the loop.
* **Cost.** At most one extra round per run, within the same per-run model,
  tool and time budgets. Rounds and retried stages are reported in
  `run_metrics`.
