"""Read-only HTTP search over a PlateWatch database: ``GET /plates/search`` and ``GET /health``.

The store is opened with SQLite ``mode=ro``, so nothing served here can change it. Plate numbers
are personal data in many jurisdictions: ``platewatch serve`` binds loopback by default and
refuses any other address unless a bearer token is configured (``PLATEWATCH_API_TOKEN``).
"""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from platewatch.contracts import SearchHit
from platewatch.core.store import PlateStore
from platewatch.core.text import normalize_plate

MIN_TOKEN_LENGTH = 32
MAX_QUERY_CHARS = 64


def api_token() -> str | None:
    """``PLATEWATCH_API_TOKEN``, stripped; None when unset. Short tokens are rejected."""
    token = os.getenv("PLATEWATCH_API_TOKEN", "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"PLATEWATCH_API_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
    return token


def create_app(db_path: str | Path, token: str | None = None) -> FastAPI:
    """Serve ``db_path`` read-only. With ``token``, every route but ``/health`` needs it."""
    store = PlateStore(db_path, read_only=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        store.close()

    app = FastAPI(
        title="PlateWatch search",
        version="0.1.0",
        description="Read-only fuzzy search over stored licence plates.",
        lifespan=lifespan,
    )

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if token is None:
            return
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(
                status_code=401,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/plates/search",
        response_model=list[SearchHit],
        dependencies=[Depends(require_token)],
        summary="Fuzzy-search stored plates (exact matches first)",
    )
    def search_plates(
        q: Annotated[
            str,
            Query(
                min_length=1,
                max_length=MAX_QUERY_CHARS,
                description="Plate text; case, spaces and punctuation are ignored",
            ),
        ],
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
        min_score: Annotated[
            float,
            Query(ge=0, le=1, description="Minimum similarity for non-exact matches"),
        ] = 0.3,
    ) -> list[SearchHit]:
        if not normalize_plate(q):
            raise HTTPException(status_code=422, detail="query contains no letters or digits")
        return store.search(q, limit=limit, min_score=min_score)

    return app
