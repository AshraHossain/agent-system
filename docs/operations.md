# Operations

## Run locally

```bash
uv sync
export NETPULSE_AUDIT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export NETPULSE_API_TOKENS='dev-operator-token-0001:operator:olga,dev-senior-token-00002:senior_operator:sam,dev-viewer-token-00003:viewer:vic'
uv run uvicorn netpulse.api.main:app --port 8000
NETPULSE_UI_TOKEN=dev-operator-token-0001 uv run streamlit run netpulse/ui/app.py
```

## Docker Compose

```bash
cp .env.example .env              # generate real tokens (see the file)
docker compose up --build         # api :8000, ui :8501, heuristic provider
docker compose --profile ollama up --build    # also starts Ollama; set NETPULSE_LLM_PROVIDER=ollama
```

Ports bind to `127.0.0.1`. Checkpoints and the audit log live in the `netpulse-db` volume
(`NETPULSE_DB=/app/.netpulse/netpulse.db`). The image contains no labels and no generator.
The image build and Compose stack were not run in the authoring environment (no Docker daemon); `docker compose config`
validates.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NETPULSE_API_TOKENS` | none (required) | `token:role:name,...` |
| `NETPULSE_AUDIT_KEY` | none (API refuses to start) | HMAC key, 32+ characters, for the audit hash chain |
| `NETPULSE_API_WORKERS` | 4 | background job threads |
| `NETPULSE_API_DOCS` | `false` | serve unauthenticated `/docs` and `/openapi.json` |
| `NETPULSE_DB` | `.netpulse/netpulse.db` | SQLite checkpoints + audit |
| `NETPULSE_LLM_PROVIDER` | `ollama` | `ollama` or `heuristic` |
| `NETPULSE_LOG_LEVEL` | `INFO` (CLI: `WARNING`) | log level |
| `NETPULSE_LOG_FORMAT` | `json` | `json` or `text` |
| `NETPULSE_LANGSMITH_TRACING` | `false` | opt-in tracing; also needs `LANGSMITH_API_KEY` |
| `NETPULSE_API_URL`, `NETPULSE_UI_TOKEN` | `http://localhost:8000`, empty | UI connection defaults |

## Logs

One JSON object per line on stderr. Node completions log `incident_id`, `node`, `outcome`, `duration_ms` and
`error_kinds`; failed background jobs log the incident and exception type. Incident text is never logged.

## CI

`.github/workflows/ci.yml`: ruff, format check, synthetic-data checksum, pytest on Python 3.11 and 3.13, then the eval
regression gate (`docs/evaluation.md`). Ollama tests are opt-in and not run in CI.

## Limits

SQLite suits a single host with a small worker pool. The in-flight job registry is per process, so run one API process
per database.

## Audit log integrity

Every audit row carries a keyed hash of its content and the previous row's hash. Verify with
`uv run netpulse audit-verify` (exit 1 on failure) or `GET /audit/verify` as a senior operator. Both print a head
(`seq:hash`).

- **Back the key up separately from the database.** Losing it makes the existing log unverifiable; leaking it lets
  whoever holds it forge a chain. Do not put it in the image or the data volume.
- **Record the head somewhere the database host cannot write** (a ticket, a WORM bucket, another host) on a schedule,
  and verify with `netpulse audit-verify --expect-head <seq>:<hash>`. Without a recorded head, deleting the newest rows
  cannot be detected.
- Rotate the key by starting a new database; the old log stays verifiable only with its old key.
