"""Read-only run lookup served next to ADK's REST API (ADR-0010).

`GET /runs/{run_id}` returns a persisted investigation's validated report. It
reads sessions and never writes, starts or resumes anything. Runs are found
under both names OpsPilot stores sessions as: the CLI/runner's own, and the one
`adk api_server` uses (it names sessions after the `adk_apps/` directory).

With a bearer token configured (``OPSPILOT_API_TOKEN`` and/or
``OPSPILOT_API_TOKEN_SHA256``), every route except ``GET /health`` requires
``Authorization: Bearer <token>`` (ADR-0013).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi import Path as PathParam
from google.adk.sessions import BaseSessionService, Session
from pydantic import BaseModel, ValidationError

from opspilot.adk.runner import APP_NAME, USER_ID, session_service
from opspilot.config import REPO_ROOT, Settings
from opspilot.contracts.report import InvestigationReport, InvestigationStatus
from opspilot.core.auth import hash_token, parse_bearer, verify_token

ADK_APP_NAME = "opspilot_ai"
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
PUBLIC_PATH = "/health"  # liveness probes cannot send tokens; it returns only {"status": "ok"}
log = logging.getLogger("opspilot.api")


class BearerTokenAuth:
    """Require `Authorization: Bearer <token>` on every HTTP and websocket request.

    Plain ASGI rather than `BaseHTTPMiddleware`: it must cover websockets (`/run_live`)
    and must not buffer streaming responses (`/run_sse`). Accepts a token itself
    and/or SHA-256 digests of tokens; compares digests in constant time. The only
    exemption is `GET /health`.
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
        if kind == "http" and scope["method"] == "GET" and scope["path"] == PUBLIC_PATH:
            await self.app(scope, receive, send)
            return
        values = [v.decode("latin-1") for k, v in scope["headers"] if k.lower() == b"authorization"]
        token = parse_bearer(values)
        if token is not None and verify_token(token, self.digests):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            # Closing before accept refuses the handshake (HTTP 403).
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail":"missing or invalid bearer token"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
            (b"cache-control", b"no-store"),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class RunView(BaseModel):
    run_id: str
    dataset_id: str | None
    status: InvestigationStatus | str
    updated: float | None
    report: InvestigationReport | None


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
    if settings.token_digests:
        app.add_middleware(BearerTokenAuth, digests=settings.token_digests)
    return app
