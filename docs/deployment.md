# Deployment

OpsPilot is **local by default**. Nothing in this repository deploys it to a
shared environment. To expose it beyond loopback, use `opspilot serve` with
bearer tokens (see "Shared use" below); `adk web` and `adk api_server` have no
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

`opspilot serve` runs the same ADK REST API on `OPSPILOT_SESSION_DB` and adds one
read-only route, `GET /runs/{run_id}`, which returns a persisted investigation's
validated report (`404` for an unknown id, `status: "incomplete"` with no report
while a run has not finished). It finds runs started by the CLI and by the ADK API
([ADR-0010](adr/0010-read-only-run-lookup.md)):

```bash
uv run opspilot serve --port 8000
curl -s http://127.0.0.1:8000/runs/<session_id> | jq '.status, .report.investigation_summary'
```

It binds loopback by default. Any other `--host` needs tokens (next section).

`adk_apps/opspilot_ai/agent.py` exposes both `root_agent` and an `App`
(`app`) carrying the `BudgetPlugin`, so budgets and tool allowlists also apply
under `adk web` / `adk api_server`. The dataset for dev-UI sessions is
`OPSPILOT_DEFAULT_DATASET` (default `C02`) unless the session is created with
`{"dataset_id": "C05"}` in its initial state.

## Shared use: authentication

`opspilot serve` can require a bearer token on everything it serves
([ADR-0012](adr/0012-bearer-token-authentication.md)):

```bash
uv run opspilot token                      # prints a token and its digest
export OPSPILOT_API_TOKEN_SHA256=<digest>  # server side: the digest only
uv run opspilot serve --host 0.0.0.0

curl -H "Authorization: Bearer $TOKEN" http://server:8000/runs/<session_id>
```

* Give the **token** to clients and keep it out of the server's config; the server
  only needs the digest. Several digests (comma-separated) let you add a new token,
  move clients over, then drop the old one. Restart the server to apply a change.
* Everything is covered: ADK's `/run`, `/run_sse`, `/run_live` (websocket),
  sessions, artifacts, `/version`, `/docs`, and `/runs/{run_id}`. The one exception
  is `GET /health`, for load-balancer probes. A missing or wrong token gets `401`
  (`403` for a websocket handshake).
* `--host` anything but loopback is **refused without tokens**. Behind a proxy that
  already authenticates, `--insecure-no-auth` overrides that, with a warning.
* **TLS is not included.** Tokens cross the network in clear text, so put a
  TLS-terminating proxy in front or stay on a private network.
* **A token is full access**: it can start runs (spending model budget with a live
  provider), read every report and delete sessions. There are no roles, per-token
  identity or rate limits; failed attempts show as `401`s in the access log.
* The Docker image's default command is `adk web`, which has no authentication;
  keep publishing it on loopback only, or run `opspilot serve` instead.

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
tested here. A shared deployment would additionally need: per-user identity and
TLS in front of the API (a shared bearer token is covered above), a managed session database (`DatabaseSessionService` on
PostgreSQL/Cloud SQL or `VertexAiSessionService`), secret management (Secret
Manager), real telemetry/topology integrations with read-only credentials,
OpenTelemetry export to a managed backend (local export is covered in
[observability](observability.md)), and a re-run of the evaluation against live models.
