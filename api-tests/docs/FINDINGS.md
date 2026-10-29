# Findings — Traffic Flow API

Defects this suite found in `traffic-flow-analyzer`'s HTTP API (`trafficflow/api.py`,
`serve-api` on the synthetic feed). Each one is pinned by a strict-xfail test in
`tests/known_defects/test_known_defects.py`, which asserts the *correct* behaviour: the
xfail starts passing once the defect is fixed, strict mode fails the run, and the marker
should come off (with any matching patch in `src/api_tests/contract.py`).

Ports below are examples; the suite uses a free port.

| ID | Severity | Endpoint | Summary |
|---|---|---|---|
| KD-01 | Low | `GET /forecast/{id}` | `horizon` ≤ 0 is accepted and becomes 5; non-multiples of 5 are rounded down, and `horizon_minutes` shows the rounded value, not the requested one |
| KD-02 | **Medium** | `GET /forecast/{id}` | `horizon` has no upper bound: `horizon=10000000` returned 200 after ~88 s with a 243 MB body. One request ties up a worker, so this is a cheap denial of service |
| KD-03 | Low | `GET /history/{id}` | `days=-1` is a 200 with an empty `readings` list; `days` has no upper bound either |
| KD-04 | Low | `GET /path/{a}/{b}` | Any `weight` other than `time` is silently treated as `distance` (`weight=bogus` → 200) |
| KD-05 | Low | OpenAPI document | The 404 responses of `/forecast`, `/history` and `/path` are not documented |
| KD-06 | Low | all timestamps | Timestamps carry no UTC offset (`2026-09-16T08:30:00`), but the spec declares `format: date-time`, which RFC 3339 defines as having one. Clients cannot tell which time zone is meant |
| KD-07 | Low | `GET /history/{id}` | `readings[].ts` is `2026-09-16 08:20:00` (space separator, `str(Timestamp)`), unlike the ISO `T` format used everywhere else |
| KD-08 | **Medium** | `POST /ask` | A `history` item without `role` or `content` is a **500** whose body is the bare `KeyError` (`{"detail":"'role'"}`). The schema accepts any string map, so the request is valid by the spec. Found by contract fuzzing |

## Reproduce

```bash
cd traffic-flow-analyzer && uv run trafficflow serve-api --port 8765   # terminal 1

curl -s 'localhost:8765/forecast/S1?horizon=-5'          # KD-01: 200, horizon_minutes 5
curl -s 'localhost:8765/forecast/S1?horizon=7'           # KD-01: horizon_minutes 5
curl -s -o /dev/null -w '%{http_code} %{size_download} %{time_total}s\n' \
     'localhost:8765/forecast/S1?horizon=10000000'      # KD-02: 200, ~243 MB, ~88 s
curl -s 'localhost:8765/history/S1?days=-1'              # KD-03: 200, readings []
curl -s 'localhost:8765/path/S1/S4?weight=bogus'         # KD-04: 200, distance route
curl -s localhost:8765/openapi.json | jq '.paths["/forecast/{segment_id}"].get.responses|keys'  # KD-05
curl -s localhost:8765/status | jq .as_of                # KD-06: no offset
curl -s 'localhost:8765/history/S1?days=0.01' | jq '.readings[0].ts'                           # KD-07
curl -s -XPOST localhost:8765/ask -H 'content-type: application/json' \
     -d '{"question":"hi","history":[{"content":"x"}]}' # KD-08: 500 {"detail":"'role'"}
```

## Suggested fixes (in the target, not this suite)

- KD-01/02/03: bound the query parameters with FastAPI, e.g.
  `horizon: int = Query(60, ge=5, le=180, multiple_of=5)` and `days: float = Query(1.0, gt=0, le=30)`.
  The agent tools in `trafficflow/agents/tools.py` already bound `horizon_minutes` to 5–180,
  so the HTTP layer should match them.
- KD-04: `weight: Literal["time", "distance"] = "time"`.
- KD-05: `responses={404: {"model": ErrorOut}}` on the three routes.
- KD-06/07: emit timezone-aware timestamps (or document them as local time and drop
  `format: date-time`), and use `ts.isoformat()` in `/history`.
- KD-08: type the history items (`class Turn(BaseModel): role: Literal["user", "assistant"]; content: str`)
  so FastAPI returns a 422. Separately, `/status` and `/ask` return `str(exc)` as a 500 detail,
  which leaks internals for any unexpected exception.

## Not defects (checked and fine)

- Unknown segment ids, malformed parameters and wrong methods get clean 404/422/405 responses.
- No stack traces or file paths in error bodies; path-traversal and injection strings in
  segment ids are a plain 404.
- No CORS headers (no cross-origin access is granted); the `server` header names uvicorn
  without a version.
- `/stream` clamps `interval` to at least 1 s.
