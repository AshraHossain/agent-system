# API and UI (Phase 9)

All data shown is synthetic. The system is read-only; actions are only proposed.

## Run

```bash
export NETPULSE_AUDIT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export NETPULSE_API_TOKENS='dev-operator-token-0001:operator:olga,dev-senior-token-00002:senior_operator:sam,dev-viewer-token-00003:viewer:vic'
uv run uvicorn netpulse.api.main:app --port 8000
NETPULSE_UI_TOKEN=dev-operator-token-0001 uv run streamlit run netpulse/ui/app.py
```

The API refuses to start without `NETPULSE_API_TOKENS`, and refuses tokens shorter than 16 characters, tokens starting
with `change-me`, duplicate tokens, and empty tokens or names. The `dev-*` tokens above are for local use only.

## Rules

- The UI talks only to the API over HTTP (`netpulse/ui/client.py`). It never imports `netpulse.service`,
  the graph, persistence, `synthgen` or `eval`; `tests/test_ui_client.py` enforces this.
- Reviewer identity and role come only from the bearer token, never from a request body.
- Mutating endpoints return 202 and run on a background job; clients poll `GET /incidents/{id}`.
  One operation per incident at a time (409 otherwise).
- Untrusted text (descriptions, evidence summaries) is rendered with `st.text`, not markdown.
- `GET /audit/verify` (senior operators) re-verifies the audit hash chain; `/health` reports `audit_keyed`.
- Submitting an `incident_id` that already exists returns 409. More than 100 queued or running jobs returns 429.
- `/docs` and `/openapi.json` are off unless `NETPULSE_API_DOCS=true`.
