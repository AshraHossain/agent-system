# Traffic Flow API: content-keyed forecast cache for `/status` and `/forecast`

Status: approved 2026-11-23. Size: small change.

## Intent

`GET /status` slows down roughly linearly with concurrent users: the load test in
`api-tests/docs/LOADTEST.md` measured about 0.2 s for 1 request, 4.6 s at 10 concurrent and
18 s at 40. Fix that, and ship it as a PR a reviewer can approve quickly: small diff,
before/after numbers from the same load test, tests that prove correctness, and no change to
forecast results.

## What the code does today

Each `/status` call loads 7 days of history for every segment (6 in the synthetic feed), then
runs `forecast_segment` and `detect` on each. Measured per call: history about 3 ms, forecasts
about 150 ms, anomaly detection about 20 ms. The work is synchronous and CPU-bound, so
concurrent requests queue behind each other for the CPU. `/forecast/{id}` does the same for
one segment. The inputs only change when the feed has new data (every 5-minute step, or every
live-feed poll), but every request recomputes from scratch.

## Goals

- Concurrent `/status` and `/forecast` requests on unchanged data cost about one history read
  each, not a full forecast.
- Responses are identical to today's, byte for byte, for the same feed state.
- When data changes, the next request reflects it. No staleness window.

## Non-goals

- Changing forecaster or anomaly maths (the CI benchmark numbers must not move).
- Caching in the agent tools, the Gradio dashboard or `/stream`.
- Multi-process or cross-worker caching.

## Anti-goals

- A time-based cache that can serve stale `as_of` or anomalies.
- Any change to the API's response shapes or status codes.

## Approaches

1. **Content-keyed memoization (chosen).** Cache each segment's `forecast_segment` and `detect`
   results, keyed by a hash of the exact input series (values and timestamps), the segment's
   free-flow speed and the horizon. Hashing all segments costs about 1 ms. Unchanged data gives
   a hit; any new reading changes the hash and forces a recompute. Exact by construction.
2. **Time-based cache of the whole response** (for example 5 s). Smallest diff, but serves stale
   results and needs a tuning number. Rejected by the anti-goals.
3. **More uvicorn workers.** Adds CPU parallelism but every worker repeats the same work, and live
   feeds would poll the provider once per worker. A deployment knob, not a fix.
4. **Faster forecast maths.** Real gains are possible, but it touches numerics the benchmark
   tracks and makes a much larger review.

## Design

- A small cache in `trafficflow/core/` (pure Python and pandas, no FastAPI import, so the
  layering rules hold). It maps a key to a result, with a bounded size (least recently used
  entries evicted) so live feeds, whose keys change on every poll, cannot grow it without limit.
- Key: `(kind, segment_id, free_flow_kmh, horizon, content_hash)`, where `content_hash` comes
  from `pandas.util.hash_pandas_object(series, index=True)`. `kind` separates forecast entries
  from anomaly entries.
- Concurrency: one lock guards the cache, so a miss computes once and concurrent requests for
  the same key wait for that result instead of each recomputing. Holding it during a compute
  costs nothing extra: the work is CPU-bound and the GIL already serializes it. A hit is a
  dictionary lookup.
- Exceptions are never cached. A failing computation raises exactly as today (the 500 and 422
  paths are unchanged).
- Cached values are the existing frozen Pydantic models. Anomaly lists are stored as tuples and
  returned as new lists, so no caller can mutate a shared entry.
- `build_app` creates one cache per app, used by `/status` (forecast at 60 minutes plus anomalies
  per segment) and `/forecast/{id}` (forecast at the requested horizon), so `/forecast?horizon=60`
  reuses `/status`'s entries. Staleness, network status and current levels stay per request:
  they are cheap and depend on the clock.

## Verification

- Unit tests for the cache: hit returns the same result, a changed series recomputes, concurrent
  misses compute once, exceptions are not cached, size stays bounded.
- API tests: `/status` and `/forecast` responses equal the uncached computation, and after
  `feed.advance()` the response reflects the new data.
- The full `traffic-flow-analyzer` and `api-tests` suites, ruff, and the CI benchmark unchanged.
- Re-run the load test exactly as documented and update `api-tests/docs/LOADTEST.md` with
  before/after numbers. Tick the follow-up in `api-tests/PLAN.md`.

## Left to the builder

Module and class names, the exact cache size (it must comfortably hold every segment at each
horizon in use), and test layout, following existing conventions.
