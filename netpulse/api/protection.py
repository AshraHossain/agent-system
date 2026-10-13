"""Edge protections for the API: rate limits, request-size cap, security headers, HTTPS and Host enforcement.

TLS itself is terminated by a reverse proxy (Caddy in ``deploy/``) or by uvicorn's ``--ssl-*`` options; this module
makes the API refuse to be used insecurely and bounds what an abusive client can cost.

Client identity for limits is ``scope["client"]``. Behind a proxy, uvicorn replaces it with the ``X-Forwarded-For``
address and sets the scheme from ``X-Forwarded-Proto`` **only** for proxies listed in ``--forwarded-allow-ips``
(env ``FORWARDED_ALLOW_IPS``, default ``127.0.0.1``). Never set that to ``*`` where clients can reach the API
directly, or any client can claim any address and any scheme.

Rate limits are token buckets held in process memory. They are per worker process and reset on restart; with several
processes, enforce limits at the proxy too. Behind a shared NAT, one abusive user can lock out others on the same
address (see ``auth_fail_per_min``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException

_TRUE = {"1", "true", "yes", "on"}
EXEMPT_PATHS = {"/health"}  # liveness probes come from the orchestrator, often over plain http
MAX_TRACKED_KEYS = 20_000


class RateLimiter:
    """Token bucket per key. ``per_min`` tokens refill per minute up to ``burst``. Memory is bounded (LRU)."""

    def __init__(self, per_min: float, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = per_min / 60.0
        self.burst = float(burst)
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def _level(self, key: str, now: float) -> float:
        tokens, stamp = self._buckets.get(key, (self.burst, now))
        return min(self.burst, tokens + (now - stamp) * self.rate)

    def consume(self, key: str, cost: float = 1.0) -> float:
        """Take ``cost`` tokens. Returns 0.0 if allowed, else the seconds to wait."""
        now = self._clock()
        with self._lock:
            level = self._level(key, now)
            if level >= cost:
                self._buckets[key] = (level - cost, now)
                allowed = 0.0
            else:
                self._buckets[key] = (level, now)
                allowed = (cost - level) / self.rate if self.rate > 0 else 3600.0
            self._buckets.move_to_end(key)
            while len(self._buckets) > MAX_TRACKED_KEYS:
                self._buckets.popitem(last=False)
            return allowed

    def wait_needed(self, key: str, cost: float = 1.0) -> float:
        """Like ``consume`` without taking tokens."""
        now = self._clock()
        with self._lock:
            level = self._level(key, now)
            return 0.0 if level >= cost else (cost - level) / self.rate if self.rate > 0 else 3600.0


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be zero or positive")
    return value


@dataclass(frozen=True)
class Protection:
    """All values are per process. A limit of 0 disables that limit."""

    ip_per_min: int = 1200
    ip_burst: int = 200
    read_per_min: int = 600
    read_burst: int = 100
    write_per_min: int = 30
    write_burst: int = 10
    auth_fail_per_min: int = 10
    auth_fail_burst: int = 10
    max_body_bytes: int = 256 * 1024
    require_https: bool = False
    allowed_hosts: tuple[str, ...] = ()
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)

    @classmethod
    def from_env(cls, *, require_https_default: bool) -> Protection:
        env = os.environ.get
        https = env("NETPULSE_REQUIRE_HTTPS")
        return cls(
            ip_per_min=_int_env("NETPULSE_RATE_LIMIT_IP_PER_MIN", cls.ip_per_min),
            read_per_min=_int_env("NETPULSE_RATE_LIMIT_READ_PER_MIN", cls.read_per_min),
            write_per_min=_int_env("NETPULSE_RATE_LIMIT_WRITE_PER_MIN", cls.write_per_min),
            auth_fail_per_min=_int_env("NETPULSE_RATE_LIMIT_AUTH_FAIL_PER_MIN", cls.auth_fail_per_min),
            max_body_bytes=_int_env("NETPULSE_MAX_BODY_BYTES", cls.max_body_bytes),
            require_https=require_https_default
            if https is None
            else https.strip().lower() in {"1", "true", "yes", "on"},
            allowed_hosts=tuple(h.strip() for h in env("NETPULSE_ALLOWED_HOSTS", "").split(",") if h.strip()),
        )


class Limits:
    """The live limiters for one app instance."""

    def __init__(self, config: Protection) -> None:
        self.config = config
        make = lambda per_min, burst: RateLimiter(per_min, burst, config.clock) if per_min else None  # noqa: E731
        self.ip = make(config.ip_per_min, config.ip_burst)
        self.read = make(config.read_per_min, config.read_burst)
        self.write = make(config.write_per_min, config.write_burst)
        self.auth_fail = make(config.auth_fail_per_min, config.auth_fail_burst)

    def check_principal(self, name: str, method: str) -> None:
        """Per-identity limit: writes are costly (each starts background work), reads are cheap."""
        limiter = self.read if method in {"GET", "HEAD", "OPTIONS"} else self.write
        if limiter and (wait := limiter.consume(name)):
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(max(1, round(wait)))})


# --------------------------------------------------------------------------- ASGI middleware


def _client_ip(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


async def _respond(send, scope, status: int, detail: str, headers: dict[str, str] | None = None) -> None:
    body = json.dumps({"detail": detail}).encode()
    merged = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    merged.update(_security_headers(scope.get("scheme") == "https", scope.get("path", "")))
    merged.update(headers or {})
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(k.lower().encode(), v.encode()) for k, v in merged.items()],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _retry_after(wait: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, round(wait)))}


class EdgeProtectionMiddleware:
    """Runs before routing, in this order: Host check, HTTPS requirement, IP lockout/limit, body cap; adds headers."""

    def __init__(self, app, limits: Limits) -> None:
        self.app = app
        self.limits = limits
        self.config = limits.config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        config, limits = self.config, self.limits
        path = scope.get("path", "")
        guarded = path not in EXEMPT_PATHS
        ip = _client_ip(scope)

        if guarded:
            if config.allowed_hosts and _header(scope, b"host").split(":")[0].lower() not in config.allowed_hosts:
                return await _respond(send, scope, 400, "invalid host header")
            if config.require_https and scope.get("scheme") != "https":
                return await _respond(send, scope, 400, "https is required")
            if limits.auth_fail and (wait := limits.auth_fail.wait_needed(ip)):
                return await _respond(send, scope, 429, "too many failed authentications", _retry_after(wait))
            if limits.ip and (wait := limits.ip.consume(ip)):
                return await _respond(send, scope, 429, "rate limit exceeded", _retry_after(wait))
            declared = _header(scope, b"content-length")
            if config.max_body_bytes and declared.isdigit() and int(declared) > config.max_body_bytes:
                return await _respond(send, scope, 413, "request body too large")

        received = 0
        overflow = False

        async def counted_receive():
            nonlocal received, overflow
            message = await receive()
            if guarded and config.max_body_bytes and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > config.max_body_bytes:  # chunked upload past the cap: stop reading, reject below
                    overflow = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                if overflow:
                    return await _respond(send, scope, 413, "request body too large")
                if message["status"] == 401 and limits.auth_fail:
                    limits.auth_fail.consume(ip)
                have = {k for k, _ in message.get("headers", [])}
                extra = [
                    (k.lower().encode(), v.encode())
                    for k, v in _security_headers(scope.get("scheme") == "https", path).items()
                    if k.lower().encode() not in have
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            elif overflow:
                return None  # the application's own body, already replaced by the 413
            return await send(message)

        await self.app(scope, counted_receive, wrapped_send)


def _security_headers(secure: bool, path: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    if path not in {"/docs", "/redoc"}:  # the interactive docs load scripts and styles from a CDN
        headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if secure:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
