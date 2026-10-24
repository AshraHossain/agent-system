# ADR-0013: Static bearer tokens for the shared-use API

**Status:** Accepted. Closes the "no authentication" caveat of
[ADR-0008](0008-no-custom-fastapi.md) and [ADR-0010](0010-read-only-run-lookup.md)
for `opspilot serve` only.

`opspilot serve` exposes ADK's whole API (start runs, read and delete sessions, write
artifacts, websockets) plus `GET /runs/{run_id}`. That is fine on loopback and
unacceptable on a shared network.

**Decision:** an optional bearer-token check in front of everything `opspilot serve`
serves. Two implementations were written in parallel: a plain shared token
(`OPSPILOT_API_TOKEN`, `--allow-unauthenticated`) and a digest-based one with
rotation. They were combined: the plain token's variable and flag stay, and the
digest form, rotation, the `/health` exemption and the DNS-rebinding fix are added.

* **Tokens.** `opspilot token` prints a random 256-bit token and its SHA-256 digest.
  The client gets the token. The server is preferably configured with digests only
  (`OPSPILOT_API_TOKEN_SHA256`, comma-separated), so a leaked config or environment
  dump is not a credential; `OPSPILOT_API_TOKEN` (the token itself, at least 32
  characters) is accepted too and is hashed at start-up. Both may be set; any
  configured token is accepted. A fast hash is right for random tokens; these must
  never be human-chosen passwords. Several digests allow rotation without downtime
  for clients. A malformed digest or a short token stops every command with a clear
  error instead of silently disabling authentication.
* **Enforcement.** A plain ASGI middleware, outermost, so it covers HTTP, streaming
  (`/run_sse`) and websockets (`/run_live`) and runs before any ADK handler.
  `Authorization: Bearer <token>` is the only accepted form (not a query string or
  cookie); duplicate or malformed headers are refused. Missing and wrong tokens get
  an identical 401 (a refused websocket handshake gets 403). Comparison is
  constant-time over every digest.
* **One exemption:** `GET /health`, which returns only `{"status": "ok"}`, so load
  balancers can probe. `/version`, `/docs` and `/openapi.json` are protected.
* **Fail closed.** `opspilot serve` refuses any non-loopback `--host` unless a token
  is configured. `--allow-unauthenticated` overrides that, with a warning, for a
  deployment behind a proxy that authenticates. Loopback without a token still works.
* **DNS rebinding.** ADK's guard against rebound `Host` headers only runs when it is
  told the bind address. `serve` now passes it, which closes a gap in ADR-0010's
  version (a foreign `Host` on loopback used to be answered).
* **Layering.** Token primitives live in `core/auth.py` (no web framework); the
  middleware lives in `api.py`.

**What this does not give you:**

* **No authorization.** Every valid token can do everything, including starting runs
  (which spend model budget with a live provider), reading every session and
  deleting them. No roles, no per-token identity or audit trail beyond uvicorn's
  access log, no rate limiting or lockout (the 256-bit token space makes guessing
  impractical, but nothing stops a client hammering the server).
* **No TLS.** Tokens cross the network in clear text unless something in front
  terminates TLS. `opspilot serve` has no TLS options.
* **Only `opspilot serve`.** `adk web`, `adk api_server` and the Docker image's
  default `adk web` command remain unauthenticated.
* Tokens are read at start-up; changing them needs a restart.

Per-user identity (OIDC/JWT validation), roles and TLS flags are the natural next
steps if this moves beyond a shared secret.
