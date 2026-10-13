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

## Open: blocking for real use

1. **Self-asserted role in the CLI.** `netpulse review --role senior_operator` trusts the caller, and the audit records
   whatever `--reviewer` says. Anyone with the CLI and the database already holds the keys to the data, so treat it as an
   administrator tool and keep it off machines whose users must not approve. The API derives role and identity from the
   validated token.
2. **No separation of duties.** The submitter can approve their own incident (`submitted_by` is never compared with the
   reviewer). Whether to require a second person is a policy decision for you.
3. **No tenancy.** Any viewer can read every incident. Single-team only.
4. **SQLite, single process.** One writer, an in-memory job registry per process, no schema migrations, no retention or
   deletion, no backup procedure. Running `uvicorn --workers N` would split job state across processes. Upgrading
   LangGraph may invalidate stored checkpoints.

## Open: should fix

- **Queued work is lost on restart.** A job that never reached its first checkpoint disappears (404 afterwards). A run
  interrupted mid-flight is not resumed automatically; an operator must call `/resume`.
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
