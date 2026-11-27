"""Read-only run lookup, readiness probe, and ASGI security middleware (ADR-0010, ADR-0013).

`GET /runs/{run_id}` returns a persisted investigation's validated report. It
reads sessions and never writes, starts or resumes anything. Runs are found
under both names OpsPilot stores sessions as: the CLI/runner's own, and the one
`adk api_server` uses (it names sessions after the `adk_apps/` directory).

Defense-in-depth ASGI middleware stack:
- `TelemetryMiddleware`: Attaches `X-Request-ID`, `X-Process-Time-Ms`, and OWASP
  security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`,
  `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`) to every
  HTTP response.
- `RateLimitMiddleware`: Enforces sliding-window rate limiting
  (`RATE_LIMIT_PER_MINUTE`, default 120 req/min) returning HTTP 429 with
  `Retry-After: 60`.
- `BearerTokenAuth`: Timing-safe authentication via `Authorization: Bearer <token>`
  or `X-API-Key: <token>` (`hmac.compare_digest`), keeping `GET /health`,
  `GET /ready`, and `GET /readyz` unauthenticated for container probes.
- `InputBoundaryMiddleware`: Enforces path-traversal rejection (`400`), payload
  byte caps (`413`), and MIME-type allowlists (`415`).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import uuid
from collections.abc import Sequence
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi import Path as PathParam
from google.adk.sessions import BaseSessionService, Session
from pydantic import BaseModel, ConfigDict, ValidationError

from opspilot.adk.runner import APP_NAME, USER_ID, session_service
from opspilot.config import REPO_ROOT, Settings
from opspilot.contracts.report import InvestigationReport, InvestigationStatus
from opspilot.core.auth import extract_request_token, hash_token, verify_token
from opspilot.core.errors import InvalidArgument
from opspilot.core.security import (
    ALLOWED_MIME_TYPES,
    DEFAULT_MAX_PAYLOAD_BYTES,
    SlidingWindowRateLimiter,
    validate_mime_type,
    validate_payload_size,
)

ADK_APP_NAME = "opspilot_ai"
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
PUBLIC_PATH = "/health"
PUBLIC_PATHS = frozenset({"/health", "/ready", "/readyz"})  # container probes do not send tokens
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH"})
OWASP_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
)
log = logging.getLogger("opspilot.api")


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
                if b"x-response-time-ms" not in existing:
                    raw_headers.append((b"x-response-time-ms", elapsed_bytes))
                for hk, hv in OWASP_SECURITY_HEADERS:
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


class RateLimitMiddleware:
    """ASGI sliding-window rate limiter returning HTTP 429 with `Retry-After: 60`."""

    def __init__(
        self,
        app,
        limit_per_minute: int = 120,
        *,
        limiter: SlidingWindowRateLimiter | None = None,
    ):
        self.app = app
        self.limiter = limiter or SlidingWindowRateLimiter(limit_per_minute=limit_per_minute)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "GET" and scope.get("path") in PUBLIC_PATHS:
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


class InputBoundaryMiddleware:
    """ASGI boundary guard for path traversal (`400`), payload size (`413`), and MIME (`415`)."""

    def __init__(
        self,
        app,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        allowed_mime_types: frozenset[str] = ALLOWED_MIME_TYPES,
    ):
        self.app = app
        self.max_payload_bytes = max_payload_bytes
        self.allowed_mime_types = allowed_mime_types

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_path = scope.get("raw_path", b"").decode("latin-1", errors="ignore")
        path = urllib.parse.unquote(raw_path or scope.get("path", ""))
        if "\x00" in path or "\\" in path or ".." in path.split("/"):
            await _json_error(send, 400, "invalid request path")
            return
        headers = {
            k.decode("latin-1", errors="ignore").lower(): v.decode("latin-1", errors="ignore")
            for k, v in (scope.get("headers") or ())
        }
        if "content-length" in headers:
            try:
                content_length = int(headers["content-length"])
            except ValueError:
                await _json_error(send, 400, "invalid content-length header")
                return
            try:
                validate_payload_size(content_length, self.max_payload_bytes)
            except InvalidArgument as exc:
                status = 400 if content_length < 0 else 413
                await _json_error(send, status, str(exc))
                return
        method = scope.get("method", "GET").upper()
        if method in MUTATION_METHODS and "content-type" in headers:
            try:
                validate_mime_type(headers["content-type"], self.allowed_mime_types)
            except InvalidArgument as exc:
                await _json_error(send, 415, str(exc))
                return

        seen_bytes = 0

        async def guarded_receive():
            nonlocal seen_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                seen_bytes += len(chunk)
                if seen_bytes > self.max_payload_bytes:
                    raise _PayloadTooLargeError
                if method in MUTATION_METHODS and seen_bytes > 0 and "content-type" not in headers:
                    raise _UnsupportedMediaTypeError
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except _PayloadTooLargeError:
            await _json_error(
                send, 413, f"payload size exceeds limit of {self.max_payload_bytes} bytes"
            )
        except _UnsupportedMediaTypeError:
            await _json_error(send, 415, "missing Content-Type header")


class _PayloadTooLargeError(Exception):
    pass


class _UnsupportedMediaTypeError(Exception):
    pass


class BearerTokenAuth:
    """Require `Authorization: Bearer <token>` or `X-API-Key: <token>` on every HTTP/WS request.

    Plain ASGI rather than `BaseHTTPMiddleware`: it must cover websockets (`/run_live`)
    and must not buffer streaming responses (`/run_sse`). Accepts a token itself
    and/or SHA-256 digests of tokens; compares digests in constant time via
    `hmac.compare_digest`. The only exemptions are `GET /health`, `GET /ready`,
    and `GET /readyz`.
    """

    def __init__(self, app, token: str | None = None, *, digests: Sequence[str] = ()):
        self.app = app
        plain = (hash_token(token),) if token else ()
        self.digests = tuple(dict.fromkeys((*digests, *plain)))

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope["method"] == "GET" and scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        raw_headers = scope.get("headers") or ()
        auth_values = [v.decode("latin-1") for k, v in raw_headers if k.lower() == b"authorization"]
        api_key_values = [v.decode("latin-1") for k, v in raw_headers if k.lower() == b"x-api-key"]
        token = extract_request_token(auth_values, api_key_values)
        if token is not None and verify_token(token, self.digests):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            # Closing before accept refuses the handshake (HTTP 403).
            await send({"type": "websocket.close", "code": 1008})
            return
        await _json_error(
            send,
            401,
            "missing or invalid bearer token",
            extra_headers=[(b"www-authenticate", b"Bearer")],
        )


class RunView(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    dataset_id: str | None
    status: InvestigationStatus | str
    updated: float | None
    report: InvestigationReport | None


class ReadinessView(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready"]
    session_store: Literal["ok"]
    data_dir_ready: bool


async def find_run(service: BaseSessionService, run_id: str) -> Session | None:
    session = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=run_id)
    if session is not None:
        return session
    # ADK-served sessions are keyed by a client-chosen user id, so look the id up across users.
    for found in (await service.list_sessions(app_name=ADK_APP_NAME)).sessions:
        if found.id == run_id:
            return await service.get_session(
                app_name=ADK_APP_NAME, user_id=found.user_id, session_id=run_id
            )
    return None


def runs_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["opspilot"])
    service = session_service(settings)

    @router.get("/ready", response_model=ReadinessView, summary="Readiness probe")
    @router.get("/readyz", response_model=ReadinessView, summary="Readiness probe (k8s)")
    async def get_ready() -> ReadinessView:
        try:
            await service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
        except Exception as exc:
            log.error("readiness check failed on session store: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="session store unavailable") from None
        return ReadinessView(
            status="ready",
            session_store="ok",
            data_dir_ready=settings.data_dir.is_dir(),
        )

    @router.get("/runs/{run_id}", response_model=RunView, summary="Read a persisted investigation")
    async def get_run(
        run_id: str = PathParam(pattern=RUN_ID_PATTERN, max_length=128),
    ) -> RunView:
        session = await find_run(service, run_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")
        state = dict(session.state)
        report = None
        if (raw := state.get("final_report")) is not None:
            try:
                report = InvestigationReport.model_validate(raw)
            except ValidationError:
                log.error("stored report for run %s does not match the report schema", run_id)
                raise HTTPException(
                    status_code=500, detail="stored report does not match the current schema"
                ) from None
        return RunView(
            run_id=session.id,
            dataset_id=state.get("dataset_id"),
            status=report.status if report else "incomplete",
            updated=session.last_update_time,
            report=report,
        )

    return router


def create_app(settings: Settings | None = None, *, bind_host: str | None = None) -> FastAPI:
    """ADK's REST API for `adk_apps/` plus the run lookup, on one durable session store.

    `bind_host` is the address the server will listen on; ADK uses it to reject
    DNS-rebinding requests (a foreign `Host` header) on a loopback bind.
    """
    from google.adk.cli.fast_api import get_fast_api_app

    settings = settings or Settings.from_env()
    app = get_fast_api_app(
        agents_dir=str(REPO_ROOT / "adk_apps"),
        session_service_uri=settings.session_db_url,
        web=False,
        bind_host=bind_host,
    )
    app.include_router(runs_router(settings))
    # Starlette runs the last-added middleware first, so this registration order gives
    # ADR-0014's request order: Telemetry -> RateLimit -> BearerTokenAuth -> InputBoundary.
    app.add_middleware(
        InputBoundaryMiddleware,
        max_payload_bytes=settings.limits.max_payload_bytes,
    )
    if settings.token_digests:
        app.add_middleware(BearerTokenAuth, digests=settings.token_digests)
    if settings.limits.rate_limit_per_minute > 0:
        app.add_middleware(
            RateLimitMiddleware,
            limit_per_minute=settings.limits.rate_limit_per_minute,
        )
    app.add_middleware(TelemetryMiddleware)
    return app
