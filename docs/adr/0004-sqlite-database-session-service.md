# ADR-0004: DatabaseSessionService on SQLite

**Status:** Accepted

## Context
Investigations should be inspectable and resumable. ADK's
`InMemorySessionService` loses everything on exit; `VertexAiSessionService`
requires GCP.

## Decision
Use `DatabaseSessionService(db_url="sqlite+aiosqlite:///var/sessions.db")`
(requires the `google-adk[db]` extra and `aiosqlite`). Tests use
`InMemorySessionService` or a temp-file SQLite DB.

## Guarantees and limits
* Durable on local disk once each event is appended; not replicated, not
  multi-process-safe for heavy concurrent writers (SQLite).
* Resume = re-invoke the pipeline on the same session; each stage's
  `before_agent_callback` skips it if its `output_key` is already in state.
  This is our own explicit mechanism, not ADK's experimental resumability.
