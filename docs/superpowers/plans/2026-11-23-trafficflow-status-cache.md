# Traffic Flow API Status Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `GET /status` (and `GET /forecast/{id}`) from recomputing every forecast on every request, so concurrent requests on unchanged data cost about one history read each.

**Architecture:** A small thread-safe, size-bounded cache in `trafficflow/core/cache.py`, keyed by a content hash of each segment's exact input series. `build_app` creates one cache per app and routes `forecast_segment` and `detect` calls through it. Network status, staleness and current levels stay per request.

**Tech Stack:** Python 3.13, pandas (`pandas.util.hash_pandas_object`), `hashlib.blake2b`, `threading.Lock`, `collections.OrderedDict`, FastAPI, pytest, Locust (measurement only).

**Spec:** `docs/superpowers/specs/2026-11-23-trafficflow-status-cache-design.md`

## Global Constraints

- All code and tests live in `traffic-flow-analyzer/`; run every command from inside it. Never touch the root `pyproject.toml`, `uv.lock` or `conftest.py`. Docs in Task 3 also touch `api-tests/docs/LOADTEST.md` and `api-tests/PLAN.md`.
- `trafficflow/core/` must not import `fastapi`, `pydantic_ai` or `gradio` (guarded by `tests/test_agents.py`).
- No change to `trafficflow/core/forecast.py` or `trafficflow/core/anomaly.py`: forecast and anomaly results must be identical to today's for the same input, and the CI benchmark must not move.
- No change to any API response shape, status code or header.
- No time-based expiry anywhere: a cache entry is valid exactly as long as its input data is unchanged.
- Ruff, including the bandit `"S"` rules, must stay clean with no new ignores. Use `blake2b`, not `md5`/`sha1` (ruff S324).
- Conventional commits, scope `trafficflow` (or `api-tests` for files under `api-tests/`). Do not claim production readiness.
- Coverage gate stays met (`--cov-fail-under=70` in `pyproject.toml`).

## Review Focus

1. **An exception must never be cached.** A forecast that raises `ValueError` (too little history) must give `422` on `/forecast` every time and recompute each time; `/status` must still give its opaque `500`. Pinned by `test_cache_does_not_store_exceptions` (Task 1) and `test_forecast_error_is_not_cached` (Task 2).
2. **New data must show up on the very next request.** After `feed.advance(1)`, `/status`'s `as_of` and `/forecast`'s first point move forward and the forecaster runs again. Pinned by `test_status_recomputes_after_feed_advances` (Task 2).
3. **Identical data with a different free-flow speed must not reuse an entry**, because `forecast_segment` clips to it. No feed changes a segment's free-flow speed at runtime today, so no test can trigger this; it is pinned by the key including `free_flow_kmh` (Task 2's Interfaces), which the Task 2 reviewer must check. `test_status_responses_match_an_uncached_app` (Task 2) guards the general "same answer as no cache" property.
4. **Two apps must not share a cache.** Each `build_app` call owns its cache, so two feeds never see each other's results. Pinned by `test_each_app_has_its_own_cache` (Task 2).
5. **Many distinct keys must not grow memory without limit** (live feeds change key every poll; `/forecast` accepts 36 horizons). Pinned by `test_cache_evicts_least_recently_used` (Task 1).

---

### Task 1: Content-keyed result cache

**Files:**
- Create: `trafficflow/core/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `series_fingerprint(series: pd.Series) -> str`: a hex digest of the series' values and index. Equal series give equal digests; any changed value or timestamp gives a different digest.
  - `class ResultCache(maxsize: int = 1024)`: raises `ValueError` if `maxsize < 1`.
    - `get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T`: returns the cached value for `key`, or calls `compute()` once, stores and returns its result. Concurrent calls for a missing key compute it once. If `compute()` raises, nothing is stored and the exception propagates.
    - `__len__(self) -> int`.

- [ ] **Step 1: Write the failing tests in `tests/test_cache.py`**

```python
import threading
import time

import pandas as pd
import pytest

from trafficflow.core.cache import ResultCache, series_fingerprint


def _series(values, start="2026-01-05 08:00"):
    idx = pd.date_range(start, periods=len(values), freq="5min")
    return pd.Series(values, index=idx, dtype=float)


def test_fingerprint_is_stable_for_equal_series():
    assert series_fingerprint(_series([50, 51, 52])) == series_fingerprint(_series([50, 51, 52]))


def test_fingerprint_changes_when_a_value_changes():
    assert series_fingerprint(_series([50, 51, 52])) != series_fingerprint(_series([50, 51, 53]))


def test_fingerprint_changes_when_the_window_moves():
    a = _series([50, 51, 52])
    b = _series([50, 51, 52], start="2026-01-05 08:05")
    assert series_fingerprint(a) != series_fingerprint(b)


def test_cache_returns_the_stored_result_without_recomputing():
    cache, calls = ResultCache(), []
    first = cache.get_or_compute("k", lambda: calls.append(1) or object())
    second = cache.get_or_compute("k", lambda: calls.append(1) or object())
    assert first is second
    assert len(calls) == 1


def test_cache_does_not_store_exceptions():
    cache, calls = ResultCache(), []

    def boom():
        calls.append(1)
        raise ValueError("not enough history")

    for _ in range(2):
        with pytest.raises(ValueError, match="not enough history"):
            cache.get_or_compute("k", boom)
    assert len(calls) == 2
    assert len(cache) == 0


def test_concurrent_misses_compute_once():
    cache, calls, barrier = ResultCache(), [], threading.Barrier(8)
    results = []

    def slow():
        calls.append(1)
        time.sleep(0.05)
        return "value"

    def worker():
        barrier.wait()
        results.append(cache.get_or_compute("k", slow))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == ["value"] * 8


def test_cache_evicts_least_recently_used():
    cache = ResultCache(maxsize=2)
    cache.get_or_compute("a", lambda: 1)
    cache.get_or_compute("b", lambda: 2)
    cache.get_or_compute("a", lambda: 99)  # hit: "a" becomes most recent
    cache.get_or_compute("c", lambda: 3)  # evicts "b"
    assert len(cache) == 2
    assert cache.get_or_compute("a", lambda: 99) == 1
    assert cache.get_or_compute("b", lambda: 22) == 22


def test_cache_rejects_a_non_positive_size():
    with pytest.raises(ValueError):
        ResultCache(maxsize=0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cache.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'trafficflow.core.cache'`.

- [ ] **Step 3: Implement `trafficflow/core/cache.py`**

`series_fingerprint`: `hashlib.blake2b(pd.util.hash_pandas_object(series, index=True).to_numpy().tobytes(), digest_size=16).hexdigest()`. `ResultCache`: an `OrderedDict` and one `threading.Lock`; the whole of `get_or_compute` (lookup, compute on miss, insert, `move_to_end`, evict with `popitem(last=False)` while over `maxsize`) runs under the lock. Holding it during `compute()` is intended: it is what makes concurrent misses compute once, and the work is CPU-bound, so the GIL would serialize it anyway (say so in one short comment). Module docstring: one or two lines on what it is for.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_cache.py -v`
Expected: 8 passed.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

```bash
git add trafficflow/core/cache.py tests/test_cache.py
git commit -m "feat(trafficflow): add a content-keyed, size-bounded result cache"
```

---

### Task 2: Route `/status` and `/forecast` through the cache

**Files:**
- Modify: `trafficflow/api.py` (`build_app`, its `get_status` and `get_forecast` routes, and the `build_app` docstring)
- Test: `tests/test_api.py` (new section at the end, `# -- forecast cache --`)

**Interfaces:**
- Consumes: `ResultCache`, `series_fingerprint` from Task 1.
- Produces: no new public names. Inside `build_app`, one `ResultCache(maxsize=1024)` per app. Keys:
  - forecasts: `("forecast", seg.id, seg.free_flow_kmh, horizon, fingerprint)`
  - anomalies: `("anomalies", seg.id, seg.free_flow_kmh, fingerprint)`, value stored as a `tuple` of `Anomaly`.
  Compute each segment's `fingerprint` once per request, from the same 7-day `history` series passed to the forecaster.

- [ ] **Step 1: Write the failing tests at the end of `tests/test_api.py`**

These patch `trafficflow.core.forecast.forecast_segment` with a counting wrapper **before** calling `build_app`, because `build_app` imports it at call time. Add `import trafficflow.core.forecast as forecast_module` to the file's existing top-of-file imports (not mid-file, which ruff flags), then append:

```python
# -- forecast cache (recompute only when the feed has new data) ---------------


@pytest.fixture
def counted(monkeypatch):
    """Count forecaster runs for apps built after this fixture."""
    calls = []
    real = forecast_module.forecast_segment

    def counting(*args, **kwargs):
        calls.append(args[1].id)
        return real(*args, **kwargs)

    monkeypatch.setattr(forecast_module, "forecast_segment", counting)
    return calls


def _fresh_feed():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    return feed


def test_repeated_status_runs_the_forecaster_once_per_segment(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    n_segments = len(feed.segments())
    assert client.get("/status").status_code == 200
    assert client.get("/status").status_code == 200
    assert len(counted) == n_segments


def test_forecast_at_60_minutes_reuses_the_status_entry(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    client.get("/status")
    before = len(counted)
    assert client.get("/forecast/S1?horizon=60").status_code == 200
    assert len(counted) == before


def test_status_responses_match_an_uncached_app():
    cached = TestClient(build_app(_fresh_feed()))
    first = cached.get("/status").json()
    second = cached.get("/status").json()
    oracle = TestClient(build_app(_fresh_feed())).get("/status").json()
    assert first == second == oracle
    f_cached = [cached.get("/forecast/S1?horizon=30").json() for _ in range(2)]
    f_oracle = TestClient(build_app(_fresh_feed())).get("/forecast/S1?horizon=30").json()
    assert f_cached[0] == f_cached[1] == f_oracle


def test_status_recomputes_after_feed_advances(counted):
    feed = _fresh_feed()
    client = TestClient(build_app(feed))
    n_segments = len(feed.segments())
    before_status = client.get("/status").json()
    before_fc = client.get("/forecast/S1").json()
    feed.advance(1)
    after_status = client.get("/status").json()
    after_fc = client.get("/forecast/S1").json()
    assert after_status["as_of"] > before_status["as_of"]
    assert after_fc["points"][0]["ts"] > before_fc["points"][0]["ts"]
    assert len(counted) == 2 * n_segments


def test_forecast_error_is_not_cached(monkeypatch):
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise ValueError("need at least 288 readings, got 3")

    monkeypatch.setattr(forecast_module, "forecast_segment", failing)
    client = TestClient(build_app(_fresh_feed()))
    for _ in range(2):
        assert client.get("/forecast/S1").status_code == 422
    assert len(calls) == 2


def test_each_app_has_its_own_cache():
    early, late = _fresh_feed(), _fresh_feed()
    late.advance(12)
    a = TestClient(build_app(early)).get("/status").json()
    b = TestClient(build_app(late)).get("/status").json()
    assert a["as_of"] != b["as_of"]
```

- [ ] **Step 2: Run them to verify the cache tests fail**

Run: `uv run pytest tests/test_api.py -k "forecaster_once or reuses_the_status or recomputes_after or not_cached or own_cache or uncached_app" -v`
Expected: `test_repeated_status_runs_the_forecaster_once_per_segment` and `test_forecast_at_60_minutes_reuses_the_status_entry` FAIL on the call count. `test_status_recomputes_after_feed_advances` fails on its call count too (each `/forecast` adds one run today). The three behavior-preservation tests (`uncached_app`, `not_cached`, `own_cache`) already pass; that is expected: they guard against regressions.

- [ ] **Step 3: Wire the cache into `build_app` in `trafficflow/api.py`**

Import `ResultCache` and `series_fingerprint` with the other lazy `trafficflow.core` imports. Create `cache = ResultCache(maxsize=1024)` once per `build_app` call (1024 comfortably holds every segment at all 36 allowed horizons plus anomalies). In `get_status`, for each segment compute `fp = series_fingerprint(hists[sid])` once, then get the 60-minute forecast and the anomaly tuple through `cache.get_or_compute` with the keys above. In `get_forecast`, read the 7-day history once, fingerprint it, and get the forecast through the cache at the requested `horizon`. Keep the existing `try`/`except` blocks exactly where they are, so a `ValueError` still becomes `422` on `/forecast` and any exception still becomes the opaque `500` on `/status`. Leave `stale_segments`, `network_status` and the per-segment current-level loop per request. Extend the `build_app` docstring with one or two sentences: forecasts and anomaly scans are memoized per app, keyed by a hash of each segment's input data, so they rerun only when the feed has new readings.

- [ ] **Step 4: Run the new tests, then the whole file**

Run: `uv run pytest tests/test_api.py -v`
Expected: all pass, including every pre-existing test.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all pass, coverage gate met, ruff clean.

```bash
git add trafficflow/api.py tests/test_api.py
git commit -m "perf(trafficflow): reuse /status and /forecast results until the feed has new data"
```

---

### Task 3: Measure before/after and record it

**Files:**
- Modify: `api-tests/docs/LOADTEST.md` (results and "Not fixed here" sections)
- Modify: `api-tests/PLAN.md` (item 7)
- Modify: `traffic-flow-analyzer/TASK.md` (one done line)

**Interfaces:**
- Consumes: the API as changed by Task 2. No code changes in this task.

Paths below are from the repository root unless a step says otherwise. Measure one server at a time; the other stays idle.

- [ ] **Step 1: Start the "before" server from `master` in a temporary worktree**

```bash
git worktree add /tmp/tfa-before origin/master
(cd /tmp/tfa-before/traffic-flow-analyzer && uv sync && nohup uv run trafficflow serve-api --port 8001 > /tmp/tfa-before.log 2>&1 &)
```
Expected: `/tmp/tfa-before.log` shows `Uvicorn running on http://127.0.0.1:8001`.

- [ ] **Step 2: Start the "after" server from this branch**

```bash
(cd traffic-flow-analyzer && nohup uv run trafficflow serve-api --port 8002 > /tmp/tfa-after.log 2>&1 &)
```

- [ ] **Step 3: Run the `/status` concurrency probe against each port**

From `api-tests/` (it has `httpx`), for `PORT` in 8001 then 8002:

```bash
PORT=8001 uv run python - <<'EOF'
import concurrent.futures as cf, os, statistics, time
import httpx
url = f"http://127.0.0.1:{os.environ['PORT']}/status"
def one(_):
    t = time.perf_counter(); httpx.get(url, timeout=120).raise_for_status()
    return time.perf_counter() - t
for n in (1, 10, 40):
    with cf.ThreadPoolExecutor(n) as ex:
        lat = list(ex.map(one, range(n)))
    print(f"{n:>2} concurrent: median {statistics.median(lat):.2f}s  max {max(lat):.2f}s")
EOF
```
Expected: "before" grows roughly linearly with `n`, as documented. "After" stays far lower at 10 and 40. The first request on 8002 is a cold miss and costs about as much as "before" at `n=1`; record it as such.

- [ ] **Step 4: Run the documented Locust mixed run against each port**

```bash
uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:PORT --headless -u 40 -r 10 --run-time 30s --csv=var/load-PORT
```
Record `/status` and `/forecast/{id}` median and p99, and total failures, from each run's `_stats.csv`.

- [ ] **Step 5: Stop both servers and remove the worktree**

```bash
pkill -f "serve-api --port 800[12]"; git worktree remove /tmp/tfa-before
```
Expected: no `trafficflow serve-api` process left (`pgrep -f "serve-api --port 800"` prints nothing).

- [ ] **Step 6: Update the docs with the real numbers**

- `api-tests/docs/LOADTEST.md`: keep the original findings (the history of how the bottleneck was found). Add a "Fixed" section with a before/after table for both measurements (probe at 1/10/40, Locust medians and p99s), the same machine and date for both columns, one sentence on the fix (results keyed by a hash of the input data, recomputed only on new readings) and a pointer to `traffic-flow-analyzer/trafficflow/core/cache.py`. Include the probe snippet from Step 3 so a reviewer can reproduce it. In "Not fixed here", mark option 3 (memoize per-segment forecasts) as done, and keep options 1 and 2 with one line each on why they weren't needed.
- `api-tests/PLAN.md`: tick item 7 (`☐` → `☑`, matching the file's existing done marker) and add the fix's PR reference once known (leave `(PR #TBD)` for the controller to fill).
- `traffic-flow-analyzer/TASK.md`: add a done line under the existing done items: `- [x] /status and /forecast reuse forecasts until the feed has new data (cache keyed by input hash); see api-tests/docs/LOADTEST.md`.

- [ ] **Step 7: Commit**

```bash
git add api-tests/docs/LOADTEST.md api-tests/PLAN.md traffic-flow-analyzer/TASK.md
git commit -m "docs(api-tests): record before/after load-test numbers for the /status cache"
```
