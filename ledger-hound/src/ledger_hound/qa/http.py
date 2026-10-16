"""HTTP client that never throws on API errors and records every exchange.

Every request/response pair becomes an ``Exchange``. The step logs, attack
evidence and the final report are all built from these records, so whatever
the report claims can be traced back to a real request.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx


@dataclass
class Exchange:
    method: str
    path: str
    request_body: Any
    status: int  # 0 means the request never got a response (transport error)
    body: Any
    elapsed_ms: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def rejected(self) -> bool:
        return 400 <= self.status < 500

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApiClient:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self.log: list[Exchange] = []

    @classmethod
    def for_url(cls, base_url: str, timeout: float = 15.0) -> ApiClient:
        return cls(httpx.AsyncClient(base_url=base_url, timeout=timeout))

    @classmethod
    def for_app(cls, app: Any) -> ApiClient:
        """In-process client for an ASGI app (tests and the scoring harness)."""
        transport = httpx.ASGITransport(app=app)
        return cls(httpx.AsyncClient(transport=transport, base_url="http://p2p.local"))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, path: str, body: Any = None) -> Exchange:
        start = time.perf_counter()
        try:
            resp = await self._client.request(method, path, json=body)
            try:
                payload: Any = resp.json()
            except ValueError:
                payload = resp.text
            status = resp.status_code
        except httpx.HTTPError as exc:
            payload, status = {"transport_error": f"{type(exc).__name__}: {exc}"}, 0
        ex = Exchange(
            method=method.upper(),
            path=path,
            request_body=body,
            status=status,
            body=payload,
            elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        self.log.append(ex)
        return ex
