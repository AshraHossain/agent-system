# ADR-0001: One StateGraph, LLM only where language reasoning is needed

**Context.** We need reliable, bounded, testable investigations. Most stages
(validation, detection, topology, retrieval, verification, policy) are
computations, not judgments.

**Decision.** Use one explicit `StateGraph(InvestigationState)` with 14
logical stages. Only `generate_hypotheses` calls an LLM. Every conditional
edge is a pure router function over state.

**Alternatives.** A multi-agent supervisor; a ReAct tool-calling agent; plain
Python orchestration (see PLAN.md §2.1).

**Consequences.** Routing is unit-testable and LLM cost per run is bounded
and small. The system is less "autonomous": it cannot decide on its own to
call a tool we did not wire in. That is intended.
