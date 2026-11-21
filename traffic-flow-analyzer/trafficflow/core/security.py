"""Rate limiting and OWASP/telemetry response headers for the Traffic Flow API.

Rate limiting is opt-in (off unless configured); the OWASP security headers and
`X-Request-ID`/`X-Process-Time-Ms` telemetry added by `TelemetryMiddleware` are
always on and wrap every response the app produces, including ones
short-circuited by auth, the payload guard, or rate-limiting with 401/413/429.
An uncaught exception from a route handler is turned into a 500 by Starlette's
own `ServerErrorMiddleware`, which sits outside all user middleware including
`TelemetryMiddleware`, so that response does not carry these headers. The
strict `Content-Security-Policy` header is omitted on `/docs`, `/redoc`, and
`/openapi.json` (`CSP_EXEMPT_PATHS`) because Swagger UI/ReDoc load inline
scripts and styles that `default-src 'none'` would block; every other response
still gets the CSP header.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Sequence

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
OWASP_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
)
CSP_HEADER: tuple[bytes, bytes] = (
    b"content-security-policy",
    b"default-src 'none'; frame-ancestors 'none'",
)
CSP_EXEMPT_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})
log = logging.getLogger(__name__)


async def _json_error(
    send,
    status: int,
    detail: str,
    extra_headers: Sequence[tuple[bytes, bytes]] = (),
) -> None:
    body = json.dumps({"detail": detail}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def resolve_request_id(headers: Sequence[tuple[bytes, bytes]]) -> str:
    """Return a validated incoming `X-Request-ID` header or generate a fresh correlation ID."""
    for k, v in headers:
        if k.lower() == b"x-request-id":
            candidate = v.decode("latin-1", errors="ignore").strip()
            if REQUEST_ID_RE.match(candidate):
                return candidate
            break
    return f"req-{uuid.uuid4().hex[:12]}"


class TelemetryMiddleware:
    """ASGI middleware injecting correlation ID, latency, and OWASP security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = resolve_request_id(scope.get("headers") or ())
        started = time.perf_counter()

        async def send_with_telemetry(message):
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                elapsed_bytes = f"{elapsed_ms:.2f}".encode("latin-1")
                raw_headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in raw_headers}
                if b"x-request-id" not in existing:
                    raw_headers.append((b"x-request-id", request_id.encode("latin-1")))
                if b"x-process-time-ms" not in existing:
                    raw_headers.append((b"x-process-time-ms", elapsed_bytes))
                for hk, hv in OWASP_SECURITY_HEADERS:
                    if hk not in existing:
                        raw_headers.append((hk, hv))
                if scope.get("path") not in CSP_EXEMPT_PATHS:
                    hk, hv = CSP_HEADER
                    if hk not in existing:
                        raw_headers.append((hk, hv))
                message = {**message, "headers": raw_headers}
                log.info(
                    "http_request method=%s path=%s status=%s request_id=%s ms=%.2f",
                    scope.get("method", ""),
                    scope.get("path", ""),
                    message.get("status", 0),
                    request_id,
                    elapsed_ms,
                )
            await send(message)

        await self.app(scope, receive, send_with_telemetry)


MAX_REQUEST_BYTES: int = 64 * 1024


class PayloadSizeGuard:
    """ASGI middleware rejecting oversized POST bodies with HTTP 413.

    Checks only the `Content-Length` header, not actual bytes received
    (Design Decision 7): a chunked-transfer-encoding request with no, or a
    lying, `Content-Length` bypasses this guard. Building chunked-body byte
    counting is explicitly out of scope for this port.
    """

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers") or ():
            if k.lower() == b"content-length":
                try:
                    content_length = int(v)
                except ValueError:
                    break
                if content_length > self.max_bytes:
                    await _json_error(send, 413, f"payload exceeds {self.max_bytes} bytes")
                    return
                break
        await self.app(scope, receive, send)


class SlidingWindowRateLimiter:
    """Deterministic sliding-window rate limiter keyed by client identifier."""

    def __init__(self, limit_per_minute: int = 120, window_s: float = 60.0):
        if limit_per_minute < 0:
            raise ValueError("limit_per_minute must be >= 0")
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self.limit = limit_per_minute
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return `(allowed, retry_after_s)` for `client_key` at timestamp `now`."""
        if self.limit == 0:
            return True, 0
        ts = time.monotonic() if now is None else now
        cutoff = ts - self.window_s
        with self._lock:
            q = self._hits[client_key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                return False, int(self.window_s)
            q.append(ts)
            return True, 0


class RateLimitMiddleware:
    """ASGI sliding-window rate limiter returning HTTP 429 with `Retry-After: 60`."""

    def __init__(
        self,
        app,
        limit_per_minute: int = 0,
        *,
        limiter: SlidingWindowRateLimiter | None = None,
    ):
        self.app = app
        self.limiter = limiter or SlidingWindowRateLimiter(limit_per_minute=limit_per_minute)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_key = client[0] if client else "local"
        allowed, retry_after = self.limiter.allow(client_key)
        if not allowed:
            await _json_error(
                send,
                429,
                "rate limit exceeded",
                extra_headers=[(b"retry-after", str(retry_after).encode("latin-1"))],
            )
            return
        await self.app(scope, receive, send)
