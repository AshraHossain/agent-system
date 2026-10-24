# ADR-0010: Read-only run lookup next to ADK serving

**Status:** Accepted. Extends [ADR-0008](0008-no-custom-fastapi.md); does not replace it.

ADR-0008 kept ADK's `adk web` / `adk api_server` as the only serving layer and
said to revisit if a typed resource API was needed. Reading a finished
investigation back over HTTP is that need: ADK's sessions endpoint returns raw
session state (stage outputs, evidence keys, internal flags), and a client has
to know to pull `final_report` out of it and what shape it has.

**Decision:** add exactly one route, `GET /runs/{run_id}`, that returns the
persisted, schema-validated `InvestigationReport` plus `run_id`, `dataset_id`,
`status` (or `incomplete` while no final report exists) and `updated`.
`opspilot serve` starts ADK's own FastAPI app (`get_fast_api_app`) with this
route added, on the same `OPSPILOT_SESSION_DB`, so session handling is still
ADK's.

* **Read-only.** The route only reads sessions; it cannot start, resume, edit
  or delete a run. Other methods return 405. Starting runs stays with the ADK
  API and the CLI.
* **Two session namespaces.** The CLI/runner stores sessions as app `opspilot`,
  user `operator`; `adk api_server` stores them as app `opspilot_ai` under the
  client's user id. The route checks the first directly and then searches the
  second across users (a scan of that app's sessions, only on a miss).
* **Stable contract.** The stored report is re-validated on the way out. A
  stored report that no longer matches the schema (an old run after a breaking
  contract change) returns a generic 500 and logs only the run id.
* **Input.** `run_id` must match `[A-Za-z0-9_-]{1,128}` or the request is
  rejected with 422 before any lookup.

**Not solved:** there is no listing route and no pagination. Authentication was
added afterwards by [ADR-0013](0013-bearer-token-authentication.md); this decision
originally shipped without it.
