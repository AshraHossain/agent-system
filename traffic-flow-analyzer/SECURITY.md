# SECURITY.md — Threat Model & Defense-in-Depth for the Traffic Flow API

The Traffic Flow Analyzer is a **read-only** congestion-forecasting service. Its HTTP API
(`trafficflow serve-api`) adds a small set of defenses aligned with the **OWASP API Security Top 10**.
They are scoped to a demo-grade deployment and are not a production security program; see Section 3.

---

## 1. Threat Model & Mitigation Matrix

| Threat Vector (OWASP API / LLM) | Defense-in-Depth Control | Implementation | Automated Verification |
|---|---|---|---|
| **API2: Broken Authentication (`401 Unauthorized`)** | Optional static shared secret. `BearerTokenAuth` accepts exactly one `Authorization: Bearer <token>` or `X-API-Key: <token>` header (never both), stores only a SHA-256 digest of the token, and compares digests with `hmac.compare_digest`. It guards every route, including `/docs` and `/openapi.json`. The 401 body never echoes the token. Off unless `TRAFFICFLOW_API_TOKEN` or `--token` is set. | `trafficflow/core/auth.py`, `trafficflow/api.py` (`build_app(api_token=...)`), `trafficflow/cli.py` | `tests/test_api.py::test_auth_*` (includes `test_auth_accepts_the_configured_token_via_x_api_key`, `test_auth_refuses_both_bearer_and_api_key_headers_at_once`, `test_auth_covers_docs_and_openapi`, `test_auth_401_does_not_echo_the_configured_token`), `tests/test_security.py::test_extract_request_token_refuses_both_headers_present` |
| **API4: Unrestricted Resource Consumption (`429` / `413`)** | Opt-in per-client sliding-window rate limit (`SlidingWindowRateLimiter` via `RateLimitMiddleware`) that returns `429` with `Retry-After: 60`. It is disabled by default (`0`); enable it with `TRAFFICFLOW_RATE_LIMIT_PER_MINUTE` or `--rate-limit`. `PayloadSizeGuard` rejects any `POST` whose `Content-Length` exceeds 64 KiB with `413`, before auth or rate limiting run. Middleware depth (outermost to innermost) is `TelemetryMiddleware` → `PayloadSizeGuard` → `RateLimitMiddleware` → `BearerTokenAuth` → router, so rate limiting runs *before* auth: a failed-auth request still counts against that client's rate-limit budget. This is intentional — it throttles credential-guessing floods before any digest-comparison work is spent on them — not an oversight. | `trafficflow/core/security.py`, `trafficflow/api.py`, `trafficflow/cli.py` | `tests/test_security.py::test_rate_limiter_*`, `tests/test_api.py::test_rate_limit_returns_429_with_retry_after_once_exceeded`, `test_rate_limit_is_disabled_by_default`, `test_oversized_post_body_is_rejected_with_413`, `test_oversized_body_is_rejected_before_auth_runs`, `test_combined_auth_and_rate_limit_and_payload_guard_stack_together` |
| **API8: Security Misconfiguration (Response Headers & Telemetry)** | `TelemetryMiddleware` adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` to every response the app produces, including 401, 413, and 429 responses (an uncaught exception from a route handler becomes a 500 via Starlette's own `ServerErrorMiddleware`, which sits outside all user middleware, so that response lacks these headers). It also sets `X-Request-ID` and `X-Process-Time-Ms`. The CSP header is omitted (not weakened) on `/docs`, `/redoc`, and `/openapi.json`; see the design decisions below. | `trafficflow/core/security.py`, `trafficflow/api.py` | `tests/test_api.py::test_every_response_carries_owasp_and_telemetry_headers`, `tests/test_api.py::test_docs_and_openapi_omit_the_strict_csp_but_keep_other_headers`, `tests/test_api.py::test_a_429_response_still_carries_telemetry_headers` |
| **LLM01: Indirect Prompt Injection** | **N/A, by design.** `trafficflow/agents/tools.py` and `pipeline.py` never retrieve or pass through third-party or retrieved text; every tool reads only the deterministic feed and core. `/ask`'s `question` and `history` are the API caller's own first-party input, the same trust boundary as the call itself. See the design decisions below. | None (reasoned N/A, not a silent skip) | None |

### Design decisions

**Rate limiting defaults to disabled (`rate_limit_per_minute=0`).** This deliberately differs from
OpsPilot's always-on 120 requests/minute default. `api-tests`' own suite (stateful random walks,
contract fuzzing under `-n auto`, perf benchmarks) legitimately issues hundreds of calls per run
against one process from one client IP. An always-on default would make that already-shipped,
already-green suite start failing with spurious 429s. Operators who want it enabled set
`TRAFFICFLOW_RATE_LIMIT_PER_MINUTE` (or `--rate-limit`) explicitly.

**`Content-Security-Policy` is omitted (not weakened) on `/docs`, `/redoc`, and `/openapi.json`.**
Swagger UI's inline script and style and its CDN bundle need more than `default-src 'none'` allows.
Every other OWASP and telemetry header still applies on those paths unconditionally. This is a
documented tradeoff, not a silent gap.

**No `<untrusted_context>` / indirect-prompt-injection framing is added.** Nothing the agents read
comes from a third party or a retrieval step, so there is no untrusted text to isolate. Wrapping
the caller's own question as "untrusted" would be a category error, not a defense. This is recorded
as a reasoned N/A rather than a silent skip.

---

## 2. Static Security Analysis & Verification

Static security analysis via `flake8-bandit` (ruff rule set `"S"`) is enabled in
`traffic-flow-analyzer/pyproject.toml`. Run the offline security and API suites from inside this
sub-project:

```bash
cd traffic-flow-analyzer
uv run ruff check .
uv run pytest tests/test_security.py tests/test_api.py
```

---

## 3. Known Boundaries & Honest Limitations

- **One static shared secret.** From `trafficflow/core/auth.py`: "One static shared secret: enough to
  keep a demo instance off the open internet, not a production auth system (no per-client identity,
  scopes or revocation)." Rotating the token means restarting the server with a new value.
- **In-process, in-memory rate limiter.** `SlidingWindowRateLimiter` keeps its counters in the
  process. Counters reset on restart and are not shared across workers or replicas, so N workers
  allow up to N times the configured limit. Clients are keyed by TCP peer address, so clients
  behind one reverse proxy or NAT share one bucket. The limiter is off by default (see the design
  decisions above).
- **Payload guard checks `Content-Length` only.** The payload-size guard checks
  the `Content-Length` header only; a client using chunked transfer-encoding with no `Content-Length`
  bypasses it. Recorded as a known boundary, not claimed as full protection.
- **TLS is not terminated here.** Bearer tokens and API keys travel in plain HTTP headers. Put TLS
  in front of `trafficflow serve-api` before binding it to a non-loopback interface. Unlike
  OpsPilot's `opspilot serve`, `serve-api` does not refuse to start on a non-loopback host without a
  token, so an unauthenticated non-loopback instance is possible if the operator omits the token.
- **Prompt-injection heuristics are not used here.** This is an architectural N/A
  (no retrieved or third-party text reaches the model), not a tested control.
