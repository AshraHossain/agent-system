# API and UI (Phase 9)

All data shown is synthetic. The system is read-only; actions are only proposed.

## Run

```bash
export NETPULSE_API_TOKENS='op-token:operator:olga,senior-token:senior_operator:sam,view-token:viewer:vic'
uv run uvicorn netpulse.api.main:app --port 8000
NETPULSE_UI_TOKEN=op-token uv run streamlit run netpulse/ui/app.py
```

The API refuses to start without `NETPULSE_API_TOKENS`.

## Rules

- The UI talks only to the API over HTTP (`netpulse/ui/client.py`). It never imports `netpulse.service`,
  the graph, persistence, `synthgen` or `eval`; `tests/test_ui_client.py` enforces this.
- Reviewer identity and role come only from the bearer token, never from a request body.
- Mutating endpoints return 202 and run on a background job; clients poll `GET /incidents/{id}`.
  One operation per incident at a time (409 otherwise).
- Untrusted text (descriptions, evidence summaries) is rendered with `st.text`, not markdown.
- `GET /eval/reports` is empty until Phase 10.
