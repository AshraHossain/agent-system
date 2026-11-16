# ADR-0014: Defense-in-depth OWASP ASGI guardrails and frozen Pydantic contracts

**Status:** Accepted. Extends [ADR-0010](0010-read-only-run-lookup.md) and
[ADR-0013](0013-bearer-token-authentication.md); see [SECURITY.md](../../SECURITY.md).

## Context

To harden OpsPilot AI against the OWASP API Security Top 10 and OWASP Top 10 for LLM
Applications while keeping `opspilot/core/` and `opspilot/contracts/` 100% framework-free:

1. Data transfer objects and agent output contracts across `opspilot/contracts/` and
   `opspilot/core/` must be immutable so neither pipeline stages nor verification helpers
   can accidentally mutate shared state in-place.
2. Untrusted retrieved documents and tool/OCR outputs need explicit XML boundary framing
   (`<untrusted_context id="...">...</untrusted_context>`) in addition to secret
   redaction and prompt-injection pattern withholding.
3. The HTTP serving boundary (`opspilot/api.py`) needs rate limiting, payload/MIME
   validation, correlation/latency telemetry, readiness probes, and OWASP security
   headers without buffering SSE streams (`/run_sse`) or breaking WebSockets
   (`/run_live`).

## Decision

1. **Frozen Contracts & DTOs**: Every Pydantic `BaseModel` in `opspilot/contracts/` and
   `opspilot/core/` declares `model_config = ConfigDict(frozen=True)`, and every
   dataclass DTO declares `@dataclass(frozen=True)`. Updates use `.model_copy(update=...)`
   or `dataclasses.replace(...)`.
2. **`<untrusted_context>` XML Framing**: `core/security.py::wrap_untrusted_context` and
   `isolate_untrusted` strip spoofed `<untrusted_context>` and `<<untrusted_document>>`
   tags, normalize NFKC control characters, redact secrets, withhold suspected prompt
   injections, and wrap content inside `<untrusted_context id="...">...</untrusted_context>`.
3. **Pure-ASGI Middleware Stack in `opspilot/api.py`**:
   - `TelemetryMiddleware` (outermost): Validates incoming `X-Request-ID` (or generates
     `req-<uuid>`), measures `X-Process-Time-Ms` / `X-Response-Time-Ms`, and attaches
     `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
     `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and
     `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.
   - `RateLimitMiddleware`: Enforces `SlidingWindowRateLimiter` (`RATE_LIMIT_PER_MINUTE`,
     default `120` req/min) per client IP, returning HTTP `429` with `Retry-After: 60`
     while exempting `GET /health`, `GET /ready`, and `GET /readyz`.
   - `BearerTokenAuth`: Timing-safe authentication accepting `Authorization: Bearer` or
     `X-API-Key` verified via `hmac.compare_digest`.
   - `InputBoundaryMiddleware`: Rejects URL path traversal (`..`, `%2e%2e`, `\x00`, `\`)
     with `400`, payload sizes above `OPSPILOT_MAX_PAYLOAD_BYTES` (default 5 MiB) with
     `413`, and non-allowlisted mutation MIME types with `415`.
