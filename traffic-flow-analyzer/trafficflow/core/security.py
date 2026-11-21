"""Rate limiting and OWASP/telemetry response headers for the Traffic Flow API
(opt-in; off unless configured)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Sequence


async def _json_error(
    send,
    status: int,
    detail: str,
    extra_headers: Sequence[tuple[bytes, bytes]] = (),
) -> None:
    body = f'{{"detail":"{detail}"}}'.encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


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
