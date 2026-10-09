# ADR-0004: SQLite checkpointer and `interrupt()` for human approval

**Context.** Consequential actions need durable human approval that survives
process restarts.

**Decision.** Compile the graph with `langgraph-checkpoint-sqlite`'s
`SqliteSaver`, using `thread_id = incident_id`. `human_approval` calls
`interrupt(payload)`. The API resumes with `Command(resume=decision)` after
authenticating the reviewer and writing an audit record. `MemorySaver` is
used only in unit tests that do not claim durability.

**Alternatives.** MemorySaver (not durable); Postgres saver (production path,
needs a service); a custom approvals table (duplicates the checkpointer).

**Consequences.** One process should write at a time, so we use a bounded
worker pool. On resume, the node re-runs from its start, so the code before
`interrupt()` must be idempotent. Checkpoints contain incident data, so the
DB file is treated as sensitive.
