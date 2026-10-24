"""Thin httpx wrapper: base URL, timeout, optional bearer token, exchange log.

Retries cover connection failures only (the transport's ``retries``). A 5xx is
a result to assert on, never something to retry away.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Exchange:
    method: str
    url: str
    status: int
    elapsed_ms: float | None  # None for a stream still open
    body_preview: str

    def __str__(self) -> str:
        took = "open" if self.elapsed_ms is None else f"{self.elapsed_ms:.0f} ms"
        return f"{self.method} {self.url} -> {self.status} ({took}) {self.body_preview}"


class ApiClient:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.base_url = base_url
        self.history: deque[Exchange] = deque(maxlen=20)
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers=headers,
            transport=httpx.HTTPTransport(retries=2),
            event_hooks={"response": [self._record]},
        )

    def _record(self, response: httpx.Response) -> None:
        # an event stream never ends; reading it here would block forever
        streaming = response.headers.get("content-type", "").startswith("text/event-stream")
        if not streaming:
            response.read()
        self.history.append(
            Exchange(
                method=response.request.method,
                url=str(response.request.url),
                status=response.status_code,
                elapsed_ms=None if streaming else response.elapsed.total_seconds() * 1000,
                body_preview="<event stream>" if streaming else response.text[:200],
            )
        )

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self._http.request(method, path, **kwargs)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._http.get(path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._http.post(path, **kwargs)

    def stream(self, method: str, path: str, **kwargs):
        return self._http.stream(method, path, **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
