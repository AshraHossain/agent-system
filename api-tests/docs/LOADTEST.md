# Load test — Traffic Flow API

`loadtest/locustfile.py` is a [Locust](https://locust.io) load test, separate from the pytest
suite (it has no pass/fail exit code; read the latency and failure numbers). Two user types,
mixed by weight:

- `ReadApiUser` (weight 5): the ordinary request/response traffic — `/segments`, `/status`,
  `/forecast/{id}`, `/history/{id}`, `/path/{a}/{b}`, `/ask`.
- `StreamViewerUser` (weight 1): opens `/stream` and holds it open for ~15 s, like a live
  dashboard tab, to specifically stress the one endpoint with a connection open the whole visit.

## Run it

```bash
cd api-tests
uv sync --group load
cd ../traffic-flow-analyzer && uv run trafficflow serve-api --port 8000      # terminal 1
cd ../api-tests
uv run locust -f loadtest/locustfile.py --host http://localhost:8000         # terminal 2, web UI
# or headless, CI-style:
uv run locust -f loadtest/locustfile.py --host http://localhost:8000 \
    --headless -u 40 -r 10 --run-time 30s --csv=var/load
```

## Results (this sandbox; single uvicorn worker, synthetic feed, mock model)

**`/stream` alone, 60 concurrent viewers for 8 s: 0 failures**, each got the expected ~5 events
(reproduce: the async probe in the PR description, or `StreamViewerUser` on its own). It is
`async def` with `asyncio.sleep` between pushes, so it doesn't block the event loop — many
held-open connections are not, on their own, the risk.

**The mixed run (40 users, 30 s) found a real bottleneck, not in `/stream`, but in `/status`
and `/forecast`:**

| Endpoint | 40 concurrent requests |
|---|---|
| `/status` | median **9.5 s**, p99 **12 s** |
| `/forecast/{id}` | median 2.5 s, p99 5.2 s |
| `/segments`, `/history`, `/path` | sub-second to ~3 s |

Isolating `/status` alone and scaling concurrency confirms it: latency grows about linearly
with the number of concurrent requests, not sublinearly as real parallelism would give —
1 request ≈ 0.2 s, 10 concurrent ≈ 4.6 s each, 40 concurrent ≈ 18 s each. **Zero failures at
every level tested** (no 5xx, no timeouts): it queues rather than breaks, but 40 concurrent
users is a small dashboard's worth of traffic, not a stress test.

**Cause:** `GET /status` computes a forecast and runs anomaly detection for *every* segment on
every request (`trafficflow/api.py`'s `get_status`), all synchronous, CPU-bound work
(`forecast_segment`, `detect`). FastAPI runs a sync `def` route in a thread pool, so concurrent
calls compete for the same CPU instead of running in parallel; `/forecast` does the same work
for one segment and shows the same pattern at a smaller scale.

## Not fixed here

Scope here was the harness and the measurement, not a performance rewrite. The load test
earns its keep by turning a vague worry ("`/stream` holds connections open") into a specific,
measured one ("`/status` recomputes every segment's forecast from scratch, per request").
Options worth weighing, in order of how much they change:

1. Cache `/status`'s result for a few seconds — the feed does not change faster than that.
2. Run `uvicorn` with multiple workers (`--workers N`); the computation is CPU-bound, so more
   processes buy real parallelism the thread pool does not.
3. Memoize or incrementally update each segment's forecast instead of recomputing it per request.
