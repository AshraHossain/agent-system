# Session management

## Selected service

| Context | Service | Persistence |
|---|---|---|
| CLI / demo / container | `DatabaseSessionService(db_url="sqlite+aiosqlite:///var/sessions.db")` (`google-adk[db]` + `aiosqlite`) | Durable on local disk: sessions, every event, and state deltas are written to SQLite as events are appended. |
| Tests | `InMemorySessionService` (or a temp-file SQLite DB in `test_sessions.py`) | None — lost when the process exits. |
| `adk web` / `adk api_server` | ADK CLI default (in-memory unless `--session_service_uri sqlite+aiosqlite:///var/sessions.db` is passed) | As configured. |

Guarantees we rely on and test: a session written by one service instance can
be read by a fresh instance (`test_session_persists_to_sqlite_and_can_be_inspected`).
Not guaranteed: replication, multi-process write safety under load, encryption
at rest, retention/expiry.

## What is stored in state

| Key | Content | Size discipline |
|---|---|---|
| `investigation_id`, `dataset_id` | identifiers | — |
| `scope` | `InvestigationScope` (redacted request, windows, candidate services) | small |
| `evidence:<ID>` | `Evidence` (summary ≤400 chars, source ref, ≤16 scalar fields) | ≤60 per tool call, ~200 per run |
| `*_finding`, `incident_analysis`, `report_draft`, `verification`, `review` | stage outputs | bounded by schema `max_length` |
| `final_report` | `InvestigationReport` | bounded |

Raw telemetry series and full documents are **never** stored in state — only
references (`telemetry:<entity>/<metric>@<window>`, `document:<ID>`) and small
summaries. `test_state_holds_references_not_datasets` caps state at 250 KB.

## Inspect and resume

```bash
uv run opspilot list
uv run opspilot show <session_id>
uv run opspilot resume <session_id>
```

Resume semantics (`runner.resume_invalidation`): stage outputs that are
missing or `status: failed` are cleared **together with every downstream
stage**; completed upstream stages are skipped by `skip_if_done`
(returning their stored, schema-valid output), so no model or tool work is
repeated for them. Evidence is content-addressed, so re-running a stage cannot
duplicate records. A completed investigation resumes with zero model/tool
calls (`test_resume_of_completed_investigation_skips_all_model_work`); a run
that failed in the incident analyst re-runs only the analyst and later stages
(`test_resume_reruns_failed_stage_and_downstream_only`). With the optional
re-investigation loop enabled, resume also resets the `investigation_rounds`
counter, so the resumed run gets its own retry round.

This is an explicit, application-level mechanism; it does not rely on ADK's
experimental resumability features.
