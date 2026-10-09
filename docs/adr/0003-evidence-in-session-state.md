# ADR-0003: Evidence registry as content-addressed keys in session state

**Status:** Accepted

## Context
Conclusions must cite evidence that verifiably exists. Parallel agents write
concurrently. Large datasets must not be placed in conversational state.

## Decision
Core tool functions are pure and return `Evidence` objects. The ADK wrapper
writes each one to session state as `evidence:<ID>` via `tool_context.state`.
IDs are `EV-<KIND>-<sha256[:8]>` of the canonical evidence content, so they are
idempotent and collision-free across parallel branches (distinct keys merge
cleanly in event `state_delta`s). Records are small: summary, source reference,
a few numbers. A per-investigation cap bounds their number.

## Consequences
* + Evidence is persisted with the session (inspectable/resumable) without a
  second store.
* + Verification is a set-membership check.
* − Agents could cite an ID that exists but is irrelevant; the deterministic
  verifier checks existence and kind/entity consistency, not relevance —
  relevance is left to the secondary reviewer and evaluation.
