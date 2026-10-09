# ADR-0003: JSON-only state with an immutable evidence registry

**Context.** Checkpoints must be durable and readable across code changes.
Every claim must be traceable to an observation.

**Decision.** State values are `model_dump(mode="json")` dicts, validated by
Pydantic at node boundaries. `evidence_references` is a dict keyed by
`ev-<kind>-NNNN`, merged by a reducer that rejects mutation. Raw telemetry
and full documents stay in the data store and are referenced by ID or
window.

**Alternatives.** Pydantic objects in state (couples checkpoints to classes);
raw data in state (bloat, prompt size).

**Consequences.** Every node re-validates the state it reads, which costs a
little CPU. Citation validity becomes a set-membership check.
