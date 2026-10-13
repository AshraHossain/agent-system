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
| `NETPULSE_AUTH_MODE` | `tokens` | `oidc` for production; `tokens` is development only |
| `NETPULSE_API_TOKENS` | none | `tokens` mode: `token:role:name,...` |
| `NETPULSE_OIDC_ISSUER` / `_AUDIENCE` / `_ROLE_MAP` | none | `oidc` mode, all required |
| `NETPULSE_OIDC_JWKS_URL` / `_ROLE_CLAIM` / `_NAME_CLAIM` | discovery / `groups` / `sub` | `oidc` mode, optional |
| `NETPULSE_OIDC_TENANT_CLAIM` | none | claim naming the caller's tenant; setting it turns on multi-tenancy |
| `NETPULSE_TENANT_DATASETS` | none | `acme=case-01,case-02;globex=case-03`: datasets each tenant may investigate (`*` = all) |
| `NETPULSE_FOUR_EYES` | `on` | `off` lets a submitter approve their own incident (labs only) |
| `NETPULSE_REQUIRE_HTTPS` | `true` in oidc mode | refuse requests whose scheme is not https (`/health` exempt) |
| `NETPULSE_ALLOWED_HOSTS` | any | comma-separated Host header allowlist |
| `NETPULSE_MAX_BODY_BYTES` | 262144 | request body cap (413) |
| `NETPULSE_RATE_LIMIT_{IP,READ,WRITE,AUTH_FAIL}_PER_MIN` | 1200 / 600 / 30 / 10 | per-process limits; 0 disables |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | uvicorn: the only proxies whose `X-Forwarded-*` headers are believed |
| `NETPULSE_UI_TOKEN_HEADER` | empty | UI: read the bearer token from this proxy-set header instead of a text box |
| `NETPULSE_AUDIT_KEY` | none (API refuses to start) | HMAC key, 32+ characters, for the audit hash chain |
| `NETPULSE_API_WORKERS` | 4 | job worker threads **per process** |
| `NETPULSE_JOB_LEASE_SECONDS` | 60 | how long a lost worker's job waits before another process takes it over |
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

## Processes, durability and recovery

Run as many API processes as you need **on one host** (`uvicorn ... --workers N`, or several containers sharing one
volume on local disk). They share one SQLite file:

- **Job queue in the database.** Each submit, approval and resume is a row in `netpulse_jobs`; worker threads in every
  process claim rows. All processes report the same `job_state`, and the database refuses a second active job for an
  incident (409) even when two requests reach different processes at the same instant.
- **Queued work survives restarts.** Jobs are picked up by whichever process is running.
- **A killed worker's job is recovered.** A running job holds a lease that its process renews every third of
  `NETPULSE_JOB_LEASE_SECONDS`. If the process dies, another claims the job when the lease expires and continues from the
  last checkpoint (the audit log shows a `resumed` event). After 3 lost workers the job is marked failed
  ("abandoned"). A job that merely raises is failed, not retried. A worker that hangs without dying keeps renewing its
  lease, so a hung job is not recovered.
- **Capacity:** 100 queued or running jobs overall and 25 per tenant, counted across all processes.
- **Per-process limits.** The request rate limits and the failed-authentication lockout are held in each process's memory,
  so with N processes the effective limits are about N times those configured. Set them lower, or also limit at the proxy.
- **Local disk only.** Never put the database on NFS or another network filesystem; do not run replicas on several hosts
  against it. That needs a different database (see `docs/production_readiness.md`).
- **Schema versions.** NetPulse's tables are migrated automatically at startup under the write lock, so a rolling restart
  or `--workers N` cannot apply a migration twice. A database written by a *newer* NetPulse is refused. Roll back by
  restoring a backup taken before the upgrade, not by running old code on a migrated file.

### Backup, restore and retention

```bash
netpulse backup --to /backups/netpulse-$(date +%F).db     # online, consistent, owner-only, refuses to overwrite
netpulse audit-verify --expect-head <seq:hash>             # run against a restored copy to prove the history is intact
netpulse prune --older-than-days 90 --dry-run              # list, then run without --dry-run to delete
```

- `backup` is safe while the service runs and other processes write. It verifies the audit chain **in the copy** and exits
  1 if it fails, so a failure is a failed backup. Copy it off the host; it contains every tenant's data and reviewer
  identities. Keep `NETPULSE_AUDIT_KEY` separately: without it the chain cannot be verified.
- **Restore:** stop the service, move the live `.db`, `.db-wal` and `.db-shm` files aside, copy the backup into place
  (mode 600), start the service. Queued jobs in the backup run again; running ones are re-claimed after the lease.
- **Retention:** `prune` deletes the checkpoints and job records of incidents whose last audit event is `run_finished` and
  older than the cutoff. Paused, running and stalled incidents are never pruned. The audit log is never pruned (its hash
  chain must stay contiguous), and it keeps the incident title and reviewer identities, so apply your own retention policy
  to the audit and to backups.

## Multi-tenancy

By default there is one tenant (`default`) and nothing changes. To isolate several organizations:

- **OIDC:** set `NETPULSE_OIDC_TENANT_CLAIM` (for example `org`). Every identity must then carry a tenant in that claim
  (`[a-z0-9][a-z0-9_-]{0,31}`); one without gets 403. The tenant name `default` is refused in multi-tenant mode.
- **Static tokens (development):** a fourth field, `token:role:name:tenant`.
- **Platform operators:** a tenant named `_platform` may run `GET /audit/verify` (the chain is global, so tenant
  seniors may not) and may read or submit no incidents.
- **Datasets:** set `NETPULSE_TENANT_DATASETS`. In multi-tenant mode a tenant with no entry can analyse nothing (403).
- **Isolation model:** a tenant's incident `x` is stored as `<tenant>~x` in the checkpoints and audit log. Two tenants
  may use the same incident ID without knowing about each other. Another tenant's ID returns 404, identical to an
  ID that never existed. Job-queue slots are capped per tenant (25 of 100).
- **Four-eyes, rate limits and audit identities** all work within a tenant.

What is shared, so decide whether it is acceptable: the runbook and past-incident corpus and the topology; the SQLite file,
logs and audit chain (the platform operator sees all tenants); the evaluation reports. There is no per-tenant encryption,
backup or quota beyond the queue cap.

## Transport security and rate limiting

**TLS is terminated at a reverse proxy** (or by uvicorn: `--ssl-keyfile/--ssl-certfile`). `deploy/Caddyfile` and the
`proxy` Compose profile run Caddy, which obtains certificates automatically for public names:

```bash
export NETPULSE_UI_DOMAIN=netpulse.example.com NETPULSE_API_DOMAIN=api.netpulse.example.com
export NETPULSE_UI_API_URL=https://api.netpulse.example.com   # oidc mode refuses plain http, even from the UI
docker compose --profile proxy up --build
```

What the API enforces itself (`netpulse/api/protection.py`):

| Control | Behavior |
|---|---|
| HTTPS | `NETPULSE_REQUIRE_HTTPS` (default true in oidc mode): plain http gets 400 *before* credentials are examined. `/health` is exempt for orchestrator probes |
| HSTS and headers | `Strict-Transport-Security` over https; `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, a restrictive CSP (not on `/docs`) |
| Host header | `NETPULSE_ALLOWED_HOSTS` allowlist (400 otherwise) |
| Body size | 256 KiB cap: refused by `Content-Length` and also while streaming chunked bodies (413) |
| Per-address limit | 1200 requests/min, burst 200, including unauthenticated requests (429 + `Retry-After`) |
| Failed-auth lockout | 10 failures/min per address; while locked, **every** request from it gets 429, valid tokens included. Role denials (403) do not count |
| Per-identity limits | reads 600/min (burst 100); writes (POST: submit, approve, resume) 30/min (burst 10), per authenticated user |

**Trusted proxies are the part to get right.** Client address and scheme come from `X-Forwarded-For` and
`X-Forwarded-Proto`, which uvicorn believes only from `FORWARDED_ALLOW_IPS` (default `127.0.0.1`; Compose pins the proxy at
`172.28.0.10`). Do **not** set it to `*` while clients can reach the API directly: they could claim any address, escaping
the limits, or claim `https` to defeat the HTTPS requirement. Verified live: a header forged by an untrusted source is
ignored (400 for the scheme, and a rotating `X-Forwarded-For` does not evade the limit).

Limits to know about:

- Rate limits live in process memory: per worker process, reset on restart. With several processes or replicas, also limit
  at the proxy. Memory is bounded (20k tracked keys, least-recently-used eviction).
- Users behind one NAT share an address, so one abusive client can lock the others out for about a minute.
- The Streamlit UI has no limits of its own; limit it at the proxy.
- No CORS headers are sent. Browsers on other origins cannot call the API, and the UI calls it server-side.
- Direct TLS via uvicorn works (verified with a throwaway CA) but has no certificate renewal; prefer the proxy.
- **Not run:** `deploy/Caddyfile` and the `proxy` profile (no Caddy or Docker daemon here; the Compose file validates
  with `docker compose config`). Validate with `caddy validate` before use, and check certificate issuance for your domain.

## Identity (OIDC)

Production authenticates callers with access tokens (JWTs) from your identity provider. NetPulse issues no tokens and
sees no passwords.

```bash
export NETPULSE_AUTH_MODE=oidc
export NETPULSE_OIDC_ISSUER=https://idp.example.com
export NETPULSE_OIDC_AUDIENCE=netpulse-api
export NETPULSE_OIDC_ROLE_MAP='netpulse-viewers=viewer,netpulse-operators=operator,netpulse-seniors=senior_operator'
```

At the IdP: register an API (audience `netpulse-api`), put users in the three groups, and make the access token carry a
`groups` claim (or set `NETPULSE_OIDC_ROLE_CLAIM`). Keep access-token lifetimes short.

- **Checked on every request:** asymmetric signature against the IdP's published keys (discovered from
  `<issuer>/.well-known/openid-configuration`, or `NETPULSE_OIDC_JWKS_URL`), issuer, audience, expiry, not-before, and
  that `sub` exists. `none` and `HS*` algorithms and tokens without a `kid` are refused.
- **Roles** come only from the group claim through the role map. An authenticated user in no mapped group gets 403, not
  viewer. If several groups map, the highest role wins.
- **Audit identity** is the `sub` claim (stable), not an email or display name.
- **Keys** are cached 10 minutes; an unknown `kid` triggers at most one refetch per 30 seconds; if the IdP is unreachable
  cached keys keep working for up to 24 hours, then authentication fails closed.
- **Not covered:** no revocation or introspection. Disabling a user at the IdP takes effect when their access token
  expires.
- The issuer and JWKS URLs must be https (plain http only for localhost).
- **UI:** run Streamlit behind an authenticating proxy (for example oauth2-proxy with access-token passing) and set
  `NETPULSE_UI_TOKEN_HEADER=X-Forwarded-Access-Token`. The UI forwards that token and the API validates it, so a client
  that sets the header itself gains nothing. Bind the UI to localhost or a private network so only the proxy reaches it.
- **Not verified:** a real IdP (Keycloak, Entra ID, Okta, Auth0). Tests use a fake IdP, plus a live run against a stub
  IdP over HTTP. Claim names and group formats differ between providers; check yours.

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
