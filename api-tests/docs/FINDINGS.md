# Findings — Traffic Flow API

Defects this suite found in `traffic-flow-analyzer`'s HTTP API (`trafficflow/api.py`,
`serve-api` on the synthetic feed). While a finding is open it is pinned by a strict-xfail
test in `tests/known_defects/test_known_defects.py` that asserts the *correct* behaviour.
The xfail starts passing once the defect is fixed, strict mode fails the run, and the test
moves into the regular suite as a regression guard. Any matching patch in
`src/api_tests/contract.py` comes out at the same time.

**All nine findings are fixed.** None are open.

| ID | Severity | Endpoint | Defect | Fix | Guarded by |
|---|---|---|---|---|---|
| KD-01 | Low | `GET /forecast/{id}` | `horizon` ≤ 0 was accepted and became 5; non-multiples of 5 were rounded down, and `horizon_minutes` showed the rounded value | `horizon` must be 5–180 and a multiple of 5 (the feed's step); anything else is a 422 | `negative/test_errors.py` |
| KD-02 | **Medium** | `GET /forecast/{id}` | No upper bound: `horizon=10000000` returned 200 after ~88 s with a 243 MB body, a cheap denial of service | Capped at 180, the agents' forecast tools' limit | `negative/test_errors.py` |
| KD-03 | Low | `GET /history/{id}` | `days=-1` was a 200 with empty `readings`; no upper bound either | `days` must be > 0 and ≤ 30 | `negative/test_errors.py` |
| KD-04 | Low | `GET /path/{a}/{b}` | Any `weight` other than `time` was silently treated as `distance` | `weight` is `time` or `distance`; anything else is a 422 | `negative/test_errors.py` |
| KD-05 | Low | OpenAPI document | The 404s of `/forecast`, `/history` and `/path` were undocumented | Documented with an `ErrorOut` body | `contract/test_spec.py` |
| KD-06 | Low | all timestamps | Timestamps carried no UTC offset but the spec declared `format: date-time` (RFC 3339 requires one), so clients could not tell the time zone | The spec now describes them as feed-local ISO 8601 without an offset. Values are unchanged: every feed keeps a naive local clock, so an offset would have been invented | `contract/test_spec.py` |
| KD-07 | Low | `GET /history/{id}` | `readings[].ts` was `2026-09-16 08:20:00` (space separator), unlike the ISO `T` format elsewhere | Readings are a typed model and serialise as ISO 8601 | `functional/test_reads.py` |
| KD-08 | **Medium** | `POST /ask` | A `history` item without `role` or `content` was a **500** echoing the `KeyError` (`{"detail":"'role'"}`). Found by contract fuzzing | History items are a typed model with both fields required; incomplete ones are a 422 | `negative/test_errors.py` |
| KD-09 | Low | `POST /ask` | A body that is not valid UTF-8 got FastAPI's own 400, which the spec did not document. Found by negative-mode fuzzing once KD-01..08 were fixed | The 400 is documented | `contract/test_openapi_fuzz.py` |

## How they were reproduced (before the fixes)

```bash
cd traffic-flow-analyzer && uv run trafficflow serve-api --port 8765   # terminal 1

curl -s 'localhost:8765/forecast/S1?horizon=-5'          # KD-01: 200, horizon_minutes 5
curl -s 'localhost:8765/forecast/S1?horizon=7'           # KD-01: horizon_minutes 5
curl -s -o /dev/null -w '%{http_code} %{size_download} %{time_total}s\n' \
     'localhost:8765/forecast/S1?horizon=10000000'      # KD-02: 200, ~243 MB, ~88 s
curl -s 'localhost:8765/history/S1?days=-1'              # KD-03: 200, readings []
curl -s 'localhost:8765/path/S1/S4?weight=bogus'         # KD-04: 200, distance route
curl -s localhost:8765/openapi.json | jq '.paths["/forecast/{segment_id}"].get.responses|keys'  # KD-05
curl -s localhost:8765/status | jq .as_of                # KD-06: no offset, spec says date-time
curl -s 'localhost:8765/history/S1?days=0.01' | jq '.readings[0].ts'                           # KD-07
curl -s -XPOST localhost:8765/ask -H 'content-type: application/json' \
     -d '{"question":"hi","history":[{"content":"x"}]}' # KD-08: 500 {"detail":"'role'"}
printf ':\xff\xfe' | curl -s -XPOST localhost:8765/ask -H 'content-type: application/json' \
     --data-binary @-                                    # KD-09: 400, not in the spec
```

Each of these now returns a documented 4xx, or the documented value.

## Noted, not numbered

- **Fixed:** `/status` and `/ask` used to return `str(exc)` as the detail of a 500, which would
  leak internals (paths, credentials in messages) for any unexpected exception. No input found
  by this suite reached it, so it got no KD number. Both now log the exception with a short
  reference and answer `{"detail": "Internal error (ref …)"}`; the 500 is documented.
  Unit-tested in `traffic-flow-analyzer/tests/test_api.py` by forcing a failure.

## Not defects (checked and fine)

- Unknown segment ids, malformed parameters and wrong methods get clean 404/422/405 responses.
- No stack traces or file paths in error bodies; path-traversal and injection strings in
  segment ids are a plain 404.
- No CORS headers (no cross-origin access is granted); the `server` header names uvicorn
  without a version.
- `/stream` clamps `interval` to at least 1 s.
