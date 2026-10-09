# Deployment

OpsPilot is **local by default**. Nothing in this repository deploys it to a
shared environment, and it should not be exposed beyond loopback without adding
authentication.

## Local (recommended)

```bash
uv sync
uv run opspilot build-data
uv run opspilot demo --case C03                       # CLI demo
uv run adk web adk_apps                               # dev UI, 127.0.0.1:8000
uv run adk api_server adk_apps \
  --session_service_uri sqlite+aiosqlite:///var/sessions.db   # REST + durable sessions
```

`adk_apps/opspilot_ai/agent.py` exposes both `root_agent` and an `App`
(`app`) carrying the `BudgetPlugin`, so budgets and tool allowlists also apply
under `adk web` / `adk api_server`. The dataset for dev-UI sessions is
`OPSPILOT_DEFAULT_DATASET` (default `C02`) unless the session is created with
`{"dataset_id": "C05"}` in its initial state.

## Container

```bash
docker build -t opspilot .
docker run --rm -p 127.0.0.1:8000:8000 opspilot                  # mock model
docker run --rm -p 127.0.0.1:8000:8000 \
  -e OPSPILOT_MODEL_PROVIDER=gemini -e GOOGLE_API_KEY opspilot     # live Gemini
```

* Python 3.11-slim, non-root user (uid 10001), datasets generated at build
  time, `adk web` on port 8000. The container binds `0.0.0.0` internally;
  exposure is controlled by `-p 127.0.0.1:...`.
* Credentials are passed at runtime only. `.dockerignore` excludes `.env*`.
* Behind a TLS-intercepting proxy, pass its CA as a BuildKit secret:
  `docker build --secret id=extra_ca,src=/path/ca.pem -t opspilot .` (the CA
  is mounted only during the build steps, not stored in a layer).
* Sessions in the container are written to `/app/var/sessions.db` by the CLI;
  mount a volume on `/app/var` to keep them.

## CI

`.github/workflows/ci.yml`: Ruff lint/format, the offline pytest suite (no
credentials), the offline evaluation + guardrail perturbations (uploaded as an
artifact), and a Docker build with an `adk api_server` smoke test.

## Beyond local (not implemented)

`adk deploy cloud_run` / Agent Engine exist in ADK 2.11.0 but were not used or
tested here. A shared deployment would additionally need: authentication in
front of the API, a managed session database (`DatabaseSessionService` on
PostgreSQL/Cloud SQL or `VertexAiSessionService`), secret management (Secret
Manager), real telemetry/topology integrations with read-only credentials,
OpenTelemetry export, and a re-run of the evaluation against live models.
