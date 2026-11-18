"""Load test for the Traffic Flow API (not part of the pytest suite: run with Locust).

    uv sync --group load
    uv run trafficflow serve-api --port 8000             # terminal 1, in traffic-flow-analyzer/
    uv run locust -f loadtest/locustfile.py --host http://localhost:8000           # terminal 2
    uv run locust -f loadtest/locustfile.py --host http://localhost:8000 \\
        --headless -u 50 -r 10 --run-time 1m --csv=var/load                        # CI-style run

Two user types, mixed by their `weight`:

- `ReadApiUser` calls the ordinary request/response endpoints — the traffic a
  dashboard or another service would generate.
- `StreamViewerUser` opens `/stream` and holds it open for a while, like a
  live dashboard tab. Each one ties up a connection for its whole visit, which
  is the risk `/stream` adds that the other endpoints don't: enough concurrent
  viewers and the server runs out of connections or workers before it runs out
  of CPU. `ReadApiUser` alone will not surface that.

Set `API_TOKEN` to load-test an instance started with `TRAFFICFLOW_API_TOKEN`.

No assertions here beyond "the request succeeded" — Locust reports failure
rate and latency percentiles; read those, don't expect a pass/fail exit code
the way the pytest suite gives one.
"""

from __future__ import annotations

import os
import time

from locust import HttpUser, between, task

API_TOKEN = os.environ.get("API_TOKEN")
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
STREAM_SECONDS = float(os.environ.get("LOAD_STREAM_SECONDS", "15"))


class ReadApiUser(HttpUser):
    """The ordinary request/response traffic: one call, one response, repeat."""

    weight = 5
    wait_time = between(0.2, 1.5)

    def on_start(self) -> None:
        response = self.client.get("/segments", headers=AUTH_HEADERS, name="/segments")
        self.segment_ids = [s["id"] for s in response.json()] if response.ok else ["S1"]
        self._next_segment = 0

    def _segment(self) -> str:
        """Round-robin through the known segments, so one user's calls spread out."""
        segment_id = self.segment_ids[self._next_segment % len(self.segment_ids)]
        self._next_segment += 1
        return segment_id

    @task(5)
    def status(self):
        self.client.get("/status", headers=AUTH_HEADERS)

    @task(5)
    def forecast(self):
        self.client.get(
            f"/forecast/{self._segment()}",
            params={"horizon": 60},
            headers=AUTH_HEADERS,
            name="/forecast/[id]",
        )

    @task(3)
    def history(self):
        segment_id = self._segment()
        self.client.get(
            f"/history/{segment_id}",
            params={"days": 1},
            headers=AUTH_HEADERS,
            name="/history/[id]",
        )

    @task(2)
    def path(self):
        if len(self.segment_ids) < 2:
            return
        self.client.get(
            f"/path/{self.segment_ids[0]}/{self.segment_ids[-1]}",
            headers=AUTH_HEADERS,
            name="/path/[a]/[b]",
        )

    @task(2)
    def ask(self):
        self.client.post(
            "/ask",
            json={"question": "How is traffic right now?"},
            headers=AUTH_HEADERS,
            name="/ask",
        )


class StreamViewerUser(HttpUser):
    """A live-dashboard tab: opens `/stream` and holds the connection open."""

    weight = 1
    wait_time = between(1, 3)

    @task
    def watch(self):
        started = time.monotonic()
        with self.client.get(
            "/stream",
            params={"interval": 2},
            headers=AUTH_HEADERS,
            stream=True,
            name="/stream",
            catch_response=True,
        ) as response:
            if not response.ok:
                response.failure(f"status {response.status_code}")
                return
            events = 0
            try:
                for line in response.iter_lines():
                    if line.startswith(b"data:"):
                        events += 1
                    if time.monotonic() - started >= STREAM_SECONDS:
                        break
            except Exception as exc:  # connection dropped, read timeout, ...
                response.failure(str(exc))
                return
            if events == 0:
                response.failure("no events received")
