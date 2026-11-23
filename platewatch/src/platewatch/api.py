"""Read-only HTTP surface over the plate store: `GET /plates/search`, plus probes.

Read-only by construction: the only store methods reached are `search` and `count`, so no request
can write, ingest or delete. The database path comes from the process that starts the server,
never from a request, exactly as the agent's inbox is fixed in `PlateDeps`.

Authentication follows the sibling OpsPilot service: a client sends `Authorization: Bearer
<token>` or `X-API-Key: <token>`, and the server stores only SHA-256 digests of accepted tokens
(`PLATEWATCH_API_TOKEN_SHA256`), so a leaked environment dump does not leak a credential. Probes
stay open because containers do not send tokens. Binding beyond loopback without a configured
token is refused rather than quietly serving an open endpoint.

Not implemented here, deliberately: rate limiting, per-user identity and TLS. Put this behind a
reverse proxy that provides them before exposing it to a network.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Sequence

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from platewatch.contracts import SearchHit
from platewatch.core.store import PlateStore

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
PUBLIC_PATHS = frozenset({"/health", "/ready"})
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_QUERY_CHARS = 64
MAX_LIMIT = 50
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_digests(raw: str | None) -> tuple[str, ...]:
    """Digests from a comma- or whitespace-separated setting; raises on a malformed entry."""
    digests: list[str] = []
    for position, entry in enumerate([e for e in re.split(r"[,\s]+", raw or "") if e], start=1):
        digest = entry.lower()
        if not DIGEST_RE.match(digest):
            raise ValueError(
                f"PLATEWATCH_API_TOKEN_SHA256 entry {position} is not a SHA-256 hex digest "
                "(64 hex characters)"
            )
        if digest not in digests:
            digests.append(digest)
    return tuple(digests)


def verify_token(token: str, digests: Sequence[str]) -> bool:
    """Constant-time check against every configured digest, with no early exit."""
    if not token or not digests:
        return False
    presented = hash_token(token)
    matched = False
    for digest in digests:
        matched |= hmac.compare_digest(presented, digest)
    return matched


def request_token(request: Request) -> str:
    """The token from `Authorization: Bearer <token>` or `X-API-Key`, empty when absent."""
    header = request.headers.get("authorization", "")
    scheme, separator, token = header.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return request.headers.get("x-api-key", "").strip()


def build_app(store: PlateStore, digests: Sequence[str] = ()) -> FastAPI:
    """A read-only app over `store`. With no digests, every request is accepted."""
    app = FastAPI(
        title="PlateWatch read-only API",
        description="Fuzzy search over stored licence plates. No endpoint writes.",
        version="0.1.0",
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        protected = digests and request.url.path not in PUBLIC_PATHS
        if protected and not verify_token(request_token(request), digests):
            response = JSONResponse({"detail": "unauthorized"}, status_code=401)
            response.headers["www-authenticate"] = "Bearer"
            response.headers.update(SECURITY_HEADERS)
            return response
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/health", summary="Liveness probe (unauthenticated)")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", summary="Readiness probe: the store answers (unauthenticated)")
    def ready() -> dict[str, object]:
        try:
            return {"status": "ready", "plates": store.count()}
        except Exception as exc:  # the store is the only dependency worth reporting on
            raise HTTPException(
                status_code=503, detail=f"store unavailable: {type(exc).__name__}"
            ) from exc

    @app.get("/plates/search", response_model=list[SearchHit], summary="Fuzzy-search plates")
    def search_plates(
        q: str = Query(min_length=1, max_length=MAX_QUERY_CHARS, description="Plate text"),
        limit: int = Query(5, ge=1, le=MAX_LIMIT),
    ) -> list[SearchHit]:
        """Search stored plates by text. OCR confusions such as O/0 are tolerated.

        `q` is plate text only: it is normalised to A-Z0-9 and embedded, never used as a path or
        as SQL. A query that normalises to nothing returns an empty list.
        """
        return store.search(q, limit=limit)

    return app


def serve(store: PlateStore, host: str, port: int, allow_unauthenticated: bool = False) -> None:
    """Run the API. Refuses to bind beyond loopback without tokens unless told otherwise."""
    import uvicorn

    digests = parse_digests(os.getenv("PLATEWATCH_API_TOKEN_SHA256"))
    if host not in LOOPBACK_HOSTS and not digests and not allow_unauthenticated:
        raise SystemExit(
            f"refusing to serve on {host} without authentication: set "
            "PLATEWATCH_API_TOKEN_SHA256 to the SHA-256 digest of a token, bind 127.0.0.1, "
            "or pass --allow-unauthenticated if an authenticating proxy sits in front"
        )
    uvicorn.run(build_app(store, digests), host=host, port=port, log_level="info")
