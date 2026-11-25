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

## Fixed

`/status` and `/forecast/{id}` now cache each segment's forecast and anomaly result, keyed by a
hash of that segment's input data; results are recomputed only when the feed has new readings
(`traffic-flow-analyzer/trafficflow/core/cache.py`, used from `trafficflow/api.py`).

Measured on the same machine on 2026-11-25 (4 CPUs, single uvicorn worker, synthetic feed,
mock model), one server at a time, "before" from `origin/master` (30e5045) and "after" from
this branch. The Locust run is the documented one (`-u 40 -r 10 --run-time 30s`); 0 failures
in both runs.

`/status` concurrency probe (median / max per level, fresh server each; "after" is the first
probe on a new server, so its 1-concurrent row is a cold cache miss):

| Concurrent `/status` | Before | After |
|---|---|---|
| 1 | 0.35 s / 0.35 s | 0.33 s / 0.33 s (cold miss) |
| 10 | 4.09 s / 4.15 s | 0.23 s / 0.25 s |
| 40 | 16.83 s / 17.13 s | 0.70 s / 0.81 s |

A second probe run on the already-warm "after" server gave 0.05 s at 1 concurrent, 0.24 s at 10
and 0.77 s at 40. A cold miss costs about what "before" costs; the gain comes from the requests
after it.

Locust mixed run, 40 users, 30 s:

| Endpoint | Before median / p99 | After median / p99 |
|---|---|---|
| `/status` | 8.3 s / 13 s | 19 ms / 260 ms |
| `/forecast/{id}` | 3.0 s / 5.9 s | 5 ms / 170 ms |
| Requests completed (all endpoints) | 212 | 1091 |

The load is synthetic and short; these numbers show the bottleneck removed on this setup, not
that the service is ready for production traffic. Reproduce the probe from `api-tests/`, with
`PORT` set to the server under test:

```bash
PORT=8002 uv run python - <<'EOF'
import concurrent.futures as cf, os, statistics, time
import httpx

url = f"http://127.0.0.1:{os.environ['PORT']}/status"


def one(_):
    t = time.perf_counter()
    httpx.get(url, timeout=120).raise_for_status()
    return time.perf_counter() - t


for n in (1, 10, 40):
    with cf.ThreadPoolExecutor(n) as ex:
        lat = list(ex.map(one, range(n)))
    print(f"{n:>2} concurrent: median {statistics.median(lat):.2f}s  max {max(lat):.2f}s")
EOF
```

## Not fixed here (original options, status after the fix)

Scope here was the harness and the measurement, not a performance rewrite. The load test
earns its keep by turning a vague worry ("`/stream` holds connections open") into a specific,
measured one ("`/status` recomputes every segment's forecast from scratch, per request").
Options worth weighing, in order of how much they change:

1. Cache `/status`'s result for a few seconds — not needed: the input-hash cache of option 3
   already removes the repeated work without a time-based expiry.
2. Run `uvicorn` with multiple workers (`--workers N`) — not needed: with the cache, one worker
   handled the 40-user run with 0 failures; still an option if CPU-bound traffic grows.
3. Memoize or incrementally update each segment's forecast instead of recomputing it per
   request — **done**, see "Fixed" above.
