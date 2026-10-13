# Production-readiness review (Phase 12)

> NetPulse runs on **synthetic data**. This review asks what would have to change before it could be pointed at a real
> network or shared between teams. It is not a claim that it is ready.

**Verdict: suitable for a single-team demo or pilot on synthetic data. Not ready for real telemetry, multi-tenant use,
or internet exposure.** The blocking gaps are listed under "Open: blocking". Everything below was checked by running
code, unless marked "not verified".

## Fixed in this phase

| Finding | Evidence | Fix |
|---|---|---|
| Re-POSTing an existing `incident_id` replaced that run's job record; its status then showed `job_state: error` while the workflow said `completed` | Probe: a second operator resubmitted `victim` | 409 if the incident already exists |
| Job registry grew without bound (302 records after 300 rejected submissions) and the queue was unbounded | Probe | At most 100 queued or running jobs (429) and 500 finished records kept |
| Token parsing accepted duplicate tokens (the later one silently won), an empty token, an empty name | Probe | All three rejected at startup |
| `Principal` repr printed the bearer token (visible in tracebacks and logs) | Probe | `repr=False` |
| Documented example tokens were accepted by the server | `.env.example` used `change-me-*` | Tokens under 16 characters or starting `change-me` are refused; `.env.example` ships empty |
| Job error text could expose internals (SQL, paths) to any viewer | Code reading | Non-`ToolError` exceptions show only "internal error; see the server log" |
| `/docs` and `/openapi.json` were unauthenticated | Probe | Off unless `NETPULSE_API_DOCS=true` |
| SQLite file (incident data, reviewer identities) was mode 0644 | Probe | Created 0600 (WAL files inherit) |
| Importing `netpulse.api.main` built the app and required env vars as a side effect | Test failure | `app` is resolved lazily |

Each has a test in `tests/test_security.py`.

## Fixed after the review: audit log integrity

The append-only triggers stopped accidents, not an attacker with file access (dropping the trigger and rewriting `actor`
took one statement). The audit log is now an HMAC-SHA256 hash chain keyed by `NETPULSE_AUDIT_KEY`, which lives outside
the database (`docs/security.md`). Rewriting, deleting, inserting or reordering a row is detected by
`GET /audit/verify` (senior operators) or `netpulse audit-verify`, including by an attacker who recomputes every later
hash without the key. **Residual risk:** deleting the newest rows leaves a valid shorter chain, so it is detected only
against a head (`seq:hash`) you recorded elsewhere; and anyone holding the key can forge a chain. Nothing yet records
the head automatically or verifies on a schedule; that is an operational task.

## Fixed after the review: identity

Static bearer tokens had no expiry, rotation or revocation and identified no one in particular. With
`NETPULSE_AUTH_MODE=oidc` the API validates access tokens from your identity provider (signature against published keys,
issuer, audience, expiry, group-to-role mapping; audit identity is the stable `sub`). Static tokens remain for
development and the API logs a warning when they are used (`docs/operations.md`). **Residual risk:** no revocation
(access lasts until the token expires, so keep lifetimes short); never tested against a real IdP; the UI needs an
authenticating proxy in front of it for sign-in.

## Fixed after the review: transport security and rate limiting

The API refuses plain http (default in OIDC mode), sends HSTS and security headers, caps request bodies at 256 KiB
(including chunked uploads), checks the Host header, and rate-limits per address, per identity and on repeated failed
authentication. Client address and scheme are believed only from configured proxies. TLS termination is a Caddy
reverse proxy (`deploy/`, Compose `proxy` profile). Verified live against real TLS and against trusted and untrusted
proxies (`docs/operations.md`). **Residual risk:** the Caddy config and `proxy` profile have never been run; limits are
per process and reset on restart; shared-NAT lockout; the UI has no limits of its own; no WAF or DDoS protection.

## Fixed after the review: separation of duties (four-eyes)

The identity that submitted an incident can no longer approve it. It is enforced in the service and again inside the graph
(so resuming the graph directly cannot bypass it), and the API refuses a self-approval synchronously with 403. Rejecting
and requesting more investigation stay allowed, since neither authorizes anything. Denials are audited. A senior submitter
needs a different senior. **Residual risk:** identities are compared as strings, so one person with two accounts
(or two OIDC subjects) defeats it; teams with a single qualified reviewer need `NETPULSE_FOUR_EYES=off` or a second
reviewer; it does not stop two people colluding; the CLI's `--reviewer` is self-asserted, so it can name anyone.

## Fixed after the review: tenancy

Every identity now belongs to a tenant (a static-token field or an OIDC claim). A non-default tenant's incidents live in
their own key namespace (`<tenant>~<id>`), so another tenant's ID resolves to a key that does not exist: 404, with no
existence leak, and no per-endpoint ownership check that a future route could forget. A test fails if any route that
takes `{incident_id}` is not wired through the scoping dependency. Telemetry datasets are restricted per tenant
(`NETPULSE_TENANT_DATASETS`, deny by default); one tenant cannot fill the shared job queue; the global audit chain can be
verified only by `_platform` identities, who cannot read incidents. Single-tenant deployments are unchanged.
**Residual risk:** the reference corpus (runbooks, past incidents) and topology are shared knowledge, not per tenant;
the SQLite file, logs and the audit chain hold every tenant's data, so the platform operator sees everything and there is
no per-tenant encryption or backup; no per-tenant quotas beyond the queue cap; tenant membership is only as trustworthy as
the IdP claim; the CLI is a platform tool and can address any raw key; the eval reports are shared.

## Fixed after the review: running more than one process

The in-memory job registry was the reason `uvicorn --workers N` broke. Measured with 3 workers before the fix: the same
incident reported `job_state` as `None`, `running` or `done` depending on which worker answered, and 8 simultaneous
identical submissions were accepted **three times** (once per worker), two of which then failed inside a worker.
Jobs now live in the database: every process reports the same state, the database enforces one active job per incident
atomically, work queued when a process exits is picked up by the next one, and a job whose worker was killed is
re-claimed after a lease expires and continued from its checkpoint. After the fix, with the same 3 workers: consistent state,
exactly one acceptance per round, no worker failures; and with 25 jobs in flight, `kill -9` of the whole server tree
(6 running, 13 queued) was followed by a restart that completed all 25 with no errors and an intact audit chain.
Also added: versioned schema migrations (safe when many processes start at once), an online verified backup
(`netpulse backup`), and retention (`netpulse prune`, which never touches the audit log). Details in `docs/operations.md`.
**Residual risk:** one host only (below); rate limits and the failed-auth lockout are still per process, so with N
processes the effective limits are about N times higher; the migrations cover NetPulse's own tables, but a LangGraph
upgrade that changes its checkpoint format is not handled; a crash between the audit write and the first checkpoint can
repeat a `submitted` audit row; a worker that hangs without dying keeps its lease (heartbeats continue), so a stuck job
is not recovered; backups hold every tenant's data.

## Fixed after the review: the CLI's self-asserted identity

`netpulse review --reviewer X --role senior_operator` let anyone who could run it approve as anyone. Reviews through the
CLI now go to the API with the caller's token, so identity and role are the token's, and role and four-eyes are enforced by
the server; the identity flags no longer exist on that path. A development-only `--local` mode remains, disabled unless
`NETPULSE_ALLOW_LOCAL_REVIEW=true`, and every audit row records its `channel` (`api` or `local-cli`) so self-asserted
decisions are identifiable. **Residual risk:** the other CLI commands (`investigate`, `status`, `backup`, `prune`,
`audit-verify`) still open the database directly and are administrator tools, and anyone with the database file and the
audit key can still forge anything by other means; the CLI submits incidents as the fixed name `local`; `--local`
exists if someone sets the environment variable.

## Open: blocking for real use

None known.

No blocking item is known to remain on a single host, but every "fixed" section above carries residual risks that
you should read before relying on it.

## Open: only if you need more than one host

**One host is the limit.** SQLite's locking is safe between processes on the same machine and local disk, not across
machines or on a network filesystem, and its WAL cannot span hosts. Running replicas on several hosts, or high
availability if the database host dies, needs a different database (for example Postgres with LangGraph's Postgres
checkpointer and a `FOR UPDATE SKIP LOCKED` queue). That is **not implemented**; the queue and migration code are the
seams where it would plug in.

## Open: should fix

- **Timed-out nodes keep running.** Python threads cannot be killed (documented in `graph/wrapper.py`), so a hung Ollama call
  leaks a thread per timeout.
- **Supply chain.** Actions are pinned by tag, not commit SHA; Docker base images are not pinned by digest;
  `ollama/ollama:latest` floats.
- **No metrics or alerting.** Logs are structured and redacted, but there are no counters, health depth, or tracing
  outside the opt-in LangSmith path.
- **UI shows the same API error once per tab** when the token is wrong.

## What the evidence does and does not support

- Quality numbers (`docs/evaluation.md`) come from 20 synthetic cases generated by the same project that wrote the
  heuristic investigator, which may overfit the generator. They do not predict real-network performance.
- No Ollama (LLM) run has ever been made in this environment; the LLM path is exercised only by scripted fakes.
- The Docker image and Compose stack have never been built, and the GitHub Actions workflow has never run.
- `pip-audit` on the installed environment reported no known vulnerabilities at the time of the review.

## Positives worth keeping

Detection, ranking and policy are deterministic and tested; ground truth is isolated from `netpulse/` (enforced by tests
and by the Docker image contents); remediation is only proposed and `executed` is always `False`; untrusted text cannot
be sole support for a cause; paused approvals are durable across restarts; reviewer identity in the audit comes from the
authenticated token.
