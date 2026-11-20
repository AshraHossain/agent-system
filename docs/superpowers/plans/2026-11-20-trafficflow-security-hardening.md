# Traffic Flow API Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `traffic-flow-analyzer`'s HTTP API up to the same OWASP-aligned defense-in-depth standard already proven in `opspilot/` (timing-safe auth, sliding-window rate limiting, security/telemetry headers, payload bounds), replacing the lighter bearer-only auth module shipped in the still-open PR #46, scoped to this one sub-project only.

**Architecture:** Two new middleware-bearing modules mirror `opspilot/core/auth.py` and `opspilot/core/security.py` almost exactly, adapted to this API's actual surface (no probe route to exempt, no file/MIME upload surface, no retrieved/third-party text reaching the model). `trafficflow/api.py`'s `build_app` wires them in the same outermost-Telemetry / innermost-PayloadGuard order opspilot uses, so every response — including 401/413/429 short-circuits — still carries the telemetry headers.

**Tech Stack:** FastAPI, Starlette ASGI middleware, `hashlib`/`hmac` (stdlib only, no new dependency), pytest + `fastapi.testclient.TestClient`.

**Spec:** This plan's own Design Decisions section below (no separate spec file; the brainstorm that produced these decisions is captured inline since there was no prior spec document).

## Global Constraints

- Self-contained sub-project: edit only files under `traffic-flow-analyzer/`; never touch the root `pyproject.toml`/`uv.lock`/`conftest.py` (CLAUDE.md rule).
- `trafficflow/contracts/` and `trafficflow/core/` must not import `fastapi`, `pydantic_ai` or `gradio` (guarded by `tests/test_agents.py`); the new `core/auth.py` additions and `core/security.py` use only stdlib (`hashlib`, `hmac`, `re`, `time`, `threading`, `collections`), so this holds automatically — verify it still does.
- `ruff` bandit (`"S"`) ruleset is already enabled in this sub-project's `pyproject.toml`; new code must pass it with no new per-file ignores beyond what already exists (`tests/**`).
- Conventional commits; no claim of production readiness anywhere added (CLAUDE.md convention).
- Everything stays backward compatible for `API_TOKEN` unset / `rate_limit_per_minute=0`: the existing, already-merged, already-passing behavior (fully open API) must not change by default.
- Do not modify the other three sub-projects (`ledger-hound/`, `platewatch/`, `api-tests/` business logic) — per user decision, this is `traffic-flow-analyzer`-only. `api-tests/` gets exactly one new black-box test (Task 4), nothing else.
- This replaces PR #46's auth module in place, on branch `claude/determined-hamilton-q6ajd8` — not a new PR.

## Design Decisions (resolved during brainstorming, binding on every task below)

1. **No probe route exists** on this API (unlike opspilot's `/health`/`/ready`/`/readyz`), so there is nothing to exempt from auth or rate limiting. Token-unset behavior is unchanged: fully open.
2. **Rate limiting defaults to disabled (`rate_limit_per_minute=0`)**, diverging deliberately from opspilot's always-on-120 default. `api-tests`' own suite (stateful random walks, contract fuzzing under `-n auto`, perf benchmarks) legitimately issues hundreds of calls per run against one process from one client IP; an always-on default would make that *already-shipped, already-green* suite start failing with spurious 429s. Operators who want it enabled set `TRAFFICFLOW_RATE_LIMIT_PER_MINUTE` (or `--rate-limit`) explicitly.
3. **`Content-Security-Policy` is omitted (not weakened — omitted) on `/docs`, `/redoc`, and `/openapi.json`.** Swagger UI's inline script/style and CDN bundle need more than `default-src 'none'` allows; every other OWASP/telemetry header still applies there unconditionally. This is a documented tradeoff, not a silent gap.
4. **Path-traversal and MIME-allowlist guards are not built: there is no surface for them.** Every path parameter (`segment_id`, `source`, `target`) is already checked against `feed.segments()`'s fixed id set before use, never touches a filesystem or SQL string, and the only request body (`POST /ask`) is single-purpose JSON with no file/multipart upload path. Building `InputBoundaryMiddleware`-style path/MIME checks here would be dead code; `SECURITY.md` says so explicitly instead of staying silent.
5. **A payload-size cap on `POST /ask` is the one real, currently-missing input boundary**: nothing today bounds how large `question`/`history` can be before it reaches the model. Capped at `MAX_REQUEST_BYTES = 64 * 1024` (64 KiB) — comfortably larger than any real question, far below the API's existing 450 KB oversized-question test payload (`api-tests/tests/security/test_hygiene.py::test_large_question_does_not_crash_the_service`, which keeps passing unmodified: 413 < 500).
6. **No `<untrusted_context>` / indirect-prompt-injection framing is added.** `trafficflow/agents/tools.py` and `pipeline.py` never retrieve or pass through third-party/retrieved text — every tool reads only the deterministic feed/core. `/ask`'s `question` and `history` are the API caller's own first-party input, the same trust boundary as the call itself; wrapping a user's own question as "untrusted" would be a category error, not a defense. `SECURITY.md` records this as a reasoned N/A, not a silent skip.
7. **Known, documented bypass:** the payload-size guard checks the `Content-Length` header only; a client using chunked transfer-encoding with no `Content-Length` bypasses it. Recorded as a known boundary in `SECURITY.md` (same honesty standard as root `SECURITY.md`'s "Known Boundaries" section), not silently claimed as full protection.

## Review Focus

- A client sends both `Authorization` and `X-API-Key` simultaneously → must be refused (401), never silently pick one. (Task 1)
- Once rate limiting ships, the existing `api-tests` suite (hundreds of rapid calls/run) must keep passing unmodified with the new default — this is checked by *not breaking it*, not by a new assertion. (Task 2, verified in Task 6)
- `/docs` must still render a usable Swagger page after the header middleware ships, not go blank from the CSP. (Task 3)
- A `429` response must still carry `X-Request-ID` and the other telemetry headers — a rate-limited caller shouldn't lose the header a support request needs. (Task 2, once Task 3 exists — tested together in Task 3's integration test)
- A request with no `Content-Length` header (chunked transfer) must not crash the payload guard or hang; documented as an accepted bypass, not silently "handled." (Task 4)

---

### Task 1: Bearer-or-X-API-Key auth

**Files:**
- Modify: `traffic-flow-analyzer/trafficflow/core/auth.py`
- Modify: `traffic-flow-analyzer/trafficflow/api.py:158-178` (docstring + `build_app`'s existing `BearerTokenAuth` wiring — no signature change needed here, `api_token` stays the one parameter)
- Test: `traffic-flow-analyzer/tests/test_security.py` (new)
- Test: `traffic-flow-analyzer/tests/test_api.py` (extend the existing `TOKEN`/`auth_client` block)

**Interfaces:**
- Produces: `parse_api_key(api_key_values: Sequence[str]) -> str | None`, `extract_request_token(authorization_values: Sequence[str], api_key_values: Sequence[str] = ()) -> str | None` in `core/auth.py`, both importable alongside the existing `hash_token`, `verify_token`, `parse_bearer`.
- `BearerTokenAuth.__call__` now reads both `authorization` and `x-api-key` headers from `scope["headers"]` and calls `extract_request_token(auth_values, api_key_values)` in place of the current `parse_bearer(values)` call.

- [ ] **Step 1: Write the failing unit tests for the two new functions**

```python
# tests/test_security.py
from trafficflow.core.auth import extract_request_token, parse_api_key, parse_bearer

def test_parse_api_key_accepts_exactly_one_well_formed_header():
    assert parse_api_key(["abc123"]) == "abc123"

def test_parse_api_key_rejects_empty_whitespace_or_duplicate_headers():
    assert parse_api_key([""]) is None
    assert parse_api_key(["a b"]) is None
    assert parse_api_key(["a", "b"]) is None

def test_extract_request_token_prefers_bearer_when_only_bearer_present():
    assert extract_request_token(["Bearer tok"], []) == "tok"

def test_extract_request_token_prefers_api_key_when_only_api_key_present():
    assert extract_request_token([], ["tok"]) == "tok"

def test_extract_request_token_refuses_both_headers_present():
    assert extract_request_token(["Bearer tok"], ["tok"]) is None

def test_extract_request_token_returns_none_when_neither_present():
    assert extract_request_token([], []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_security.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_request_token'`

- [ ] **Step 3: Implement `parse_api_key` and `extract_request_token` in `trafficflow/core/auth.py`**

Copy the exact logic of `opspilot/core/auth.py`'s `parse_api_key` and `extract_request_token` (lines ~65-93 of that file), adapted to this module's single-digest `verify_token(token, digest)` signature (no change to `verify_token` itself — it already takes one digest string, not a `Sequence[str]`, since this module has no multi-token concept). Reuse the existing `MAX_TOKEN_CHARS` constant already in this file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_security.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Update `BearerTokenAuth.__call__` to use `extract_request_token`**

In `trafficflow/core/auth.py`, change the header extraction: collect `x-api-key` values the same way `authorization` values are already collected, then replace `token = parse_bearer(values)` with `token = extract_request_token(auth_values, api_key_values)`. Update the class docstring's first line from `"""Require \`Authorization: Bearer <token>\` on every HTTP request.` to mention `X-API-Key` too, matching `opspilot`'s `BearerTokenAuth` docstring wording.

- [ ] **Step 6: Write the failing integration tests in `tests/test_api.py`**

```python
def test_auth_accepts_the_configured_token_via_x_api_key(auth_client):
    r = auth_client.get("/segments", headers={"X-API-Key": TOKEN})
    assert r.status_code == 200

def test_auth_refuses_both_bearer_and_api_key_headers_at_once(auth_client):
    r = auth_client.get(
        "/segments", headers={"Authorization": f"Bearer {TOKEN}", "X-API-Key": TOKEN}
    )
    assert r.status_code == 401
```

- [ ] **Step 7: Run tests to verify they fail, then pass**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k auth -v`
Expected: fails before Step 5, passes after.

- [ ] **Step 8: Update `trafficflow/api.py`'s module docstring**

Change the line `With \`\`TRAFFICFLOW_API_TOKEN\`\` set (or \`\`--token\`\`), every route requires \`\`Authorization: Bearer <token>\`\`` to also mention `X-API-Key: <token>` as an accepted alternative, matching the new behavior.

- [ ] **Step 9: Commit**

```bash
cd traffic-flow-analyzer
git add trafficflow/core/auth.py trafficflow/api.py tests/test_security.py tests/test_api.py
git commit -m "feat(trafficflow): accept X-API-Key as an alternative to Authorization: Bearer"
```

---

### Task 2: Sliding-window rate limiting (opt-in)

**Files:**
- Create: `traffic-flow-analyzer/trafficflow/core/security.py`
- Modify: `traffic-flow-analyzer/trafficflow/api.py` (`build_app` signature + wiring)
- Modify: `traffic-flow-analyzer/trafficflow/cli.py` (`serve-api` subcommand)
- Test: `traffic-flow-analyzer/tests/test_security.py` (extend)
- Test: `traffic-flow-analyzer/tests/test_api.py` (extend)

**Interfaces:**
- Produces in `core/security.py`: `SlidingWindowRateLimiter(limit_per_minute: int = 0, window_s: float = 60.0)` with `.allow(client_key: str, *, now: float | None = None) -> tuple[bool, int]`, and `RateLimitMiddleware(app, limit_per_minute: int = 0, *, limiter: SlidingWindowRateLimiter | None = None)` (ASGI middleware class). Default `limit_per_minute=0` means "always allow" (`allow()` returns `(True, 0)` immediately when `self.limit == 0`), matching Design Decision 2.
- Consumes: none from Task 1.
- `build_app`'s new keyword `rate_limit_per_minute: int = 0`; when `> 0`, `app.add_middleware(RateLimitMiddleware, limit_per_minute=rate_limit_per_minute)` is added *before* (added-after-in-code, so inner-in-the-stack-order) `BearerTokenAuth` and *before* the `TelemetryMiddleware` added in Task 3 (Telemetry must wrap everything, added last).

- [ ] **Step 1: Write the failing unit tests for `SlidingWindowRateLimiter`**

```python
# tests/test_security.py (extend)
from trafficflow.core.security import SlidingWindowRateLimiter

def test_rate_limiter_disabled_by_default_always_allows():
    limiter = SlidingWindowRateLimiter(limit_per_minute=0)
    for _ in range(1000):
        allowed, retry_after = limiter.allow("client-a")
        assert allowed and retry_after == 0

def test_rate_limiter_allows_up_to_the_limit_then_blocks():
    limiter = SlidingWindowRateLimiter(limit_per_minute=2, window_s=60.0)
    assert limiter.allow("client-a", now=0.0) == (True, 0)
    assert limiter.allow("client-a", now=1.0) == (True, 0)
    allowed, retry_after = limiter.allow("client-a", now=2.0)
    assert not allowed and retry_after == 60

def test_rate_limiter_tracks_clients_independently():
    limiter = SlidingWindowRateLimiter(limit_per_minute=1, window_s=60.0)
    assert limiter.allow("client-a", now=0.0) == (True, 0)
    assert limiter.allow("client-b", now=0.0) == (True, 0)

def test_rate_limiter_window_slides_and_frees_capacity():
    limiter = SlidingWindowRateLimiter(limit_per_minute=1, window_s=60.0)
    assert limiter.allow("client-a", now=0.0) == (True, 0)
    allowed, _ = limiter.allow("client-a", now=30.0)
    assert not allowed
    allowed, _ = limiter.allow("client-a", now=61.0)
    assert allowed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_security.py -k rate_limiter -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trafficflow.core.security'`

- [ ] **Step 3: Implement `SlidingWindowRateLimiter` and `RateLimitMiddleware` in `trafficflow/core/security.py`**

Port `opspilot/core/security.py`'s `SlidingWindowRateLimiter` class verbatim, including its constructor default `limit_per_minute: int = 120` — this plan's own `RateLimitMiddleware` (next) is what changes the *effective* default to 0, by only ever constructing the limiter from `build_app`'s `rate_limit_per_minute` argument, never from the class's own default. The class already supports `limit_per_minute=0` meaning "always allow" (see its `if self.limit == 0: return True, 0` line). Port `opspilot/api.py`'s `RateLimitMiddleware` class, changing its constructor's `limit_per_minute: int = 120` default to `limit_per_minute: int = 0` (Design Decision 2), and dropping the `PUBLIC_PATHS` exemption branch (Design Decision 1: no such paths exist here) and its `_json_error` helper dependency — write a small local `_json_error(send, status, detail, extra_headers=())` helper in this file instead (same body as opspilot's, four lines), since `core/security.py` must not import `fastapi` and this is plain ASGI `send`. Give the module a top-of-file docstring one sentence long: "Rate limiting and OWASP/telemetry response headers for the Traffic Flow API (opt-in; off unless configured)."

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_security.py -k rate_limiter -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing integration test for 429 behavior**

```python
# tests/test_api.py
def test_rate_limit_returns_429_with_retry_after_once_exceeded(monkeypatch):
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    client = TestClient(build_app(feed, rate_limit_per_minute=2))
    assert client.get("/segments").status_code == 200
    assert client.get("/segments").status_code == 200
    r = client.get("/segments")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"


def test_rate_limit_is_disabled_by_default(client):
    for _ in range(10):
        assert client.get("/segments").status_code == 200
```

- [ ] **Step 6: Run tests to verify they fail, then wire `build_app`/implement, then pass**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k rate_limit -v`
Expected: fails (TypeError: unexpected keyword `rate_limit_per_minute`) before Step 7, passes after.

- [ ] **Step 7: Wire `RateLimitMiddleware` into `build_app` in `trafficflow/api.py`**

Add `rate_limit_per_minute: int = 0` to `build_app`'s signature. Import `RateLimitMiddleware` from `trafficflow.core.security` inside the function (same lazy-import style as the other `core.*` imports already there). After the existing `if api_token: app.add_middleware(BearerTokenAuth, ...)` block, add:
```python
if rate_limit_per_minute > 0:
    app.add_middleware(RateLimitMiddleware, limit_per_minute=rate_limit_per_minute)
```

- [ ] **Step 8: Wire the CLI flag and env var in `trafficflow/cli.py`**

In the `serve-api` subparser (alongside the existing `--token` argument), add:
```python
sa.add_argument(
    "--rate-limit", type=int, default=0,
    help="requests/minute per client before 429 (0 = disabled; env TRAFFICFLOW_RATE_LIMIT_PER_MINUTE)",
)
```
In the `serve-api` dispatch block (alongside the existing `token = a.token or os.environ.get(...)` line), add:
```python
rate_limit = a.rate_limit or int(os.environ.get("TRAFFICFLOW_RATE_LIMIT_PER_MINUTE", "0"))
```
and pass `rate_limit_per_minute=rate_limit` into the `build_api_app(...)` call alongside the existing `api_token=` keyword.

- [ ] **Step 9: Run the full `test_api.py` and `test_security.py` files**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_security.py tests/test_api.py -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
cd traffic-flow-analyzer
git add trafficflow/core/security.py trafficflow/api.py trafficflow/cli.py tests/test_security.py tests/test_api.py
git commit -m "feat(trafficflow): opt-in sliding-window rate limiting (429, Retry-After)"
```

---

### Task 3: OWASP/telemetry response headers (with a documented CSP exemption for `/docs`)

**Files:**
- Modify: `traffic-flow-analyzer/trafficflow/core/security.py` (add to the file Task 2 created)
- Modify: `traffic-flow-analyzer/trafficflow/api.py` (`build_app` wiring, always on)
- Test: `traffic-flow-analyzer/tests/test_api.py` (extend)

**Interfaces:**
- Produces: `OWASP_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...]` (4 headers: nosniff, frame-options, referrer-policy, cache-control — CSP is handled separately, see below), `CSP_HEADER: tuple[bytes, bytes]` (the 5th, content-security-policy), `CSP_EXEMPT_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})`, `TelemetryMiddleware(app)` (ASGI middleware, no constructor args beyond `app`).
- Consumes: nothing from Tasks 1-2; always added in `build_app` regardless of `api_token`/`rate_limit_per_minute`.

- [ ] **Step 1: Write the failing header-presence tests**

```python
# tests/test_api.py
EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}

def test_every_response_carries_owasp_and_telemetry_headers(client):
    r = client.get("/segments")
    assert "x-request-id" in r.headers
    assert "x-process-time-ms" in r.headers
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers[name] == value

def test_docs_and_openapi_omit_the_strict_csp_but_keep_other_headers(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert "content-security-policy" not in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"

def test_a_429_response_still_carries_telemetry_headers():
    feed = SyntheticFeed(days=21)
    feed.advance(24 * 12)
    client = TestClient(build_app(feed, rate_limit_per_minute=1))
    client.get("/segments")
    r = client.get("/segments")
    assert r.status_code == 429
    assert "x-request-id" in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k "headers or csp or telemetry" -v`
Expected: FAIL (no `x-request-id` header present yet)

- [ ] **Step 3: Implement `TelemetryMiddleware` in `trafficflow/core/security.py`**

Port `opspilot/api.py`'s `resolve_request_id` function (correlation-id generation/validation — copy verbatim, it has no FastAPI dependency, just `re`/`uuid`) and `TelemetryMiddleware` class into `core/security.py`, with one change to the `send_with_telemetry` closure: before appending the CSP header, check `if scope.get("path") not in CSP_EXEMPT_PATHS:` and only then append `CSP_HEADER`; the other four `OWASP_SECURITY_HEADERS` are appended unconditionally as opspilot already does. Keep opspilot's `log.info(...)` call, using `logging.getLogger(__name__)` at module level (matching `trafficflow/api.py`'s existing `logger = logging.getLogger(__name__)` line).

- [ ] **Step 4: Wire `TelemetryMiddleware` into `build_app`, added last (outermost)**

In `trafficflow/api.py`, import `TelemetryMiddleware` from `trafficflow.core.security` and add `app.add_middleware(TelemetryMiddleware)` as the final `add_middleware` call in `build_app`, after the `api_token`/`rate_limit_per_minute` conditional blocks (mirrors opspilot's ordering: Telemetry added last so it wraps/runs outermost, seeing every response including ones short-circuited by auth or rate-limit middleware).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k "headers or csp or telemetry" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full test file once more and commit**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -v`
Expected: all PASS

```bash
cd traffic-flow-analyzer
git add trafficflow/core/security.py trafficflow/api.py tests/test_api.py
git commit -m "feat(trafficflow): OWASP + telemetry response headers (CSP exempt on /docs, /redoc, /openapi.json)"
```

---

### Task 4: Payload-size guard for `POST /ask`

**Files:**
- Modify: `traffic-flow-analyzer/trafficflow/core/security.py` (add to the file Tasks 2-3 created)
- Modify: `traffic-flow-analyzer/trafficflow/api.py` (`build_app` wiring, always on)
- Test: `traffic-flow-analyzer/tests/test_api.py` (extend)
- Test: `api-tests/tests/security/test_hygiene.py` (one new black-box test)

**Interfaces:**
- Produces: `MAX_REQUEST_BYTES: int = 64 * 1024` and `PayloadSizeGuard(app, max_bytes: int = MAX_REQUEST_BYTES)` (ASGI middleware) in `core/security.py`.
- Consumes: nothing from Tasks 1-3; always added in `build_app`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/test_api.py
def test_oversized_post_body_is_rejected_with_413(client):
    big_question = "x" * (65 * 1024)  # just over the 64 KiB cap
    r = client.post("/ask", json={"question": big_question})
    assert r.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k oversized -v`
Expected: FAIL (200, not 413 — no guard yet)

- [ ] **Step 3: Implement `PayloadSizeGuard` in `trafficflow/core/security.py`**

Check `scope["method"] == "POST"`; read the `content-length` header from `scope["headers"]`; if present and `int(content_length) > max_bytes`, call a local `_json_error(send, 413, f"payload exceeds {max_bytes} bytes")` and return without calling `self.app(...)`. If `content-length` is absent or unparseable, pass through unguarded (Design Decision 7 — this is the documented, accepted bypass; do not attempt chunked-body accounting here). This is a 10-15 line `__call__`, no `guarded_receive` wrapper needed (unlike opspilot's version, which also streams-count — out of scope per Design Decision 7).

- [ ] **Step 4: Wire `PayloadSizeGuard` into `build_app`**

Import it alongside the others; add `app.add_middleware(PayloadSizeGuard)` — add this one *first* (before the `api_token`/`rate_limit_per_minute`/`Telemetry` blocks), so it rejects oversized bodies before spending auth/rate-limit work on them, matching opspilot's ordering (`InputBoundaryMiddleware` is opspilot's first-added middleware too).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd traffic-flow-analyzer && uv run pytest tests/test_api.py -k oversized -v`
Expected: PASS

- [ ] **Step 6: Write the failing black-box test in `api-tests`**

```python
# api-tests/tests/security/test_hygiene.py
def test_oversized_ask_question_is_rejected_with_413(api):
    response = api.post("/ask", json={"question": "x" * (65 * 1024)})
    assert response.status_code == 413
```

- [ ] **Step 7: Run it against the local server to verify it passes (no code change needed here — this exercises what Steps 3-4 already shipped)**

Run: `cd api-tests && uv run pytest tests/security/test_hygiene.py -k oversized -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd traffic-flow-analyzer
git add trafficflow/core/security.py trafficflow/api.py tests/test_api.py
git commit -m "feat(trafficflow): reject oversized POST bodies with 413 (64 KiB cap)"
cd ../api-tests
git add tests/security/test_hygiene.py
git commit -m "test(api-tests): assert the new 413 cap on oversized /ask questions"
```

---

### Task 5: `SECURITY.md` and doc links

**Files:**
- Create: `traffic-flow-analyzer/SECURITY.md`
- Modify: `traffic-flow-analyzer/CLAUDE.md` (also reached via the `AGENTS.md`/`GEMINI.md` symlinks — one edit, three files see it)
- Modify: `traffic-flow-analyzer/README.md` (the `TRAFFICFLOW_API_TOKEN` paragraph added in PR #46)
- Modify: `/home/user/agent-system/AGENTS.md:13` (root workspace table row for `traffic-flow-analyzer/`)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `traffic-flow-analyzer/SECURITY.md`**

Mirror root `SECURITY.md`'s structure (`## 1. Threat Model & Mitigation Matrix` table with the same five columns: Threat Vector, Defense-in-Depth Control, Implementation, Automated Verification; `## 2. Static Security Analysis & Verification`; `## 3. Known Boundaries & Honest Limitations`), scoped to what Tasks 1-4 actually built:
- Row: **API2: Broken Authentication** → `Authorization: Bearer` or `X-API-Key`, `hmac.compare_digest` → `trafficflow/core/auth.py`, `trafficflow/api.py` → `tests/test_security.py`, `tests/test_api.py::test_auth_*`.
- Row: **API4: Unrestricted Resource Consumption** → sliding-window rate limit (opt-in, default disabled — state Design Decision 2's reasoning verbatim) + 64 KiB payload cap on `/ask` → `trafficflow/core/security.py` → `tests/test_security.py::test_rate_limiter_*`, `tests/test_api.py::test_rate_limit_*`, `test_oversized_post_body_is_rejected_with_413`.
- Row: **API8: Security Misconfiguration** → OWASP/telemetry headers, CSP exempted on `/docs`/`/redoc`/`/openapi.json` (state Design Decision 3's reasoning) → `trafficflow/core/security.py` → `tests/test_api.py::test_every_response_carries_*`, `test_docs_and_openapi_omit_*`.
- Row: **LLM01: Indirect Prompt Injection** → **N/A, by design** — state Design Decision 6 verbatim (no retrieved/third-party text reaches the model anywhere in `trafficflow/agents/`).
- `## 2`: one line, `ruff` bandit (`"S"`) already enabled in this sub-project's `pyproject.toml`; `uv run pytest tests/test_security.py tests/test_api.py` is the verification command.
- `## 3. Known Boundaries`: (a) one static shared secret, no per-client identity/revocation (already true of the Task 1 auth, carried over honestly from the existing docstring); (b) the rate limiter is in-process and in-memory — it resets on restart and does not share state across multiple workers or replicas; (c) the payload guard checks `Content-Length` only (Design Decision 7, state verbatim); (d) TLS is not terminated here — same note as root `SECURITY.md`.

- [ ] **Step 2: Link it from `CLAUDE.md`**

Add one line near the top of `traffic-flow-analyzer/CLAUDE.md` (which `AGENTS.md` and `GEMINI.md` symlink to), e.g. after the existing "Read [PLAN.md](PLAN.md) and [ARCHITECTURE.md](ARCHITECTURE.md)..." sentence: `See [SECURITY.md](SECURITY.md) for the API's auth, rate-limit, and header guarantees.`

- [ ] **Step 3: Update the `README.md` auth paragraph**

In the paragraph added in PR #46 (starts "Set `TRAFFICFLOW_API_TOKEN` (or pass `--token`)..."), add a sentence: mention `X-API-Key: <token>` as an accepted alternative, `TRAFFICFLOW_RATE_LIMIT_PER_MINUTE`/`--rate-limit` (default disabled), and link `[SECURITY.md](SECURITY.md)`.

- [ ] **Step 4: Update the root `AGENTS.md` workspace table**

Change the `traffic-flow-analyzer/` row (currently ending `... | [traffic-flow-analyzer/CLAUDE.md](traffic-flow-analyzer/CLAUDE.md) |`) to also link `[traffic-flow-analyzer/SECURITY.md](traffic-flow-analyzer/SECURITY.md)`, matching the `opspilot/` row's existing style (`... [SECURITY.md](SECURITY.md) |`).

- [ ] **Step 5: Commit**

```bash
cd /home/user/agent-system
git add traffic-flow-analyzer/SECURITY.md traffic-flow-analyzer/CLAUDE.md traffic-flow-analyzer/README.md AGENTS.md
git commit -m "docs(trafficflow): add SECURITY.md and link it from CLAUDE.md, README.md, root AGENTS.md"
```

---

### Task 6: Full verification and PR update

**Files:** none created/modified beyond what Tasks 1-5 already touched; this task only runs commands and updates PR #46's description.

- [ ] **Step 1: Run `traffic-flow-analyzer`'s full test suite with coverage**

Run: `cd traffic-flow-analyzer && uv run ruff check . && uv run ruff format --check . && uv run pytest -q -p no:randomly`
Expected: 0 lint errors; all tests pass; coverage gate (`--cov-fail-under=70`, already in this sub-project's `pyproject.toml`) met.

- [ ] **Step 2: Run the full `api-tests` suite WITHOUT setting any rate-limit env var, to prove Design Decision 2 holds**

Run: `cd api-tests && uv run pytest -q` then `uv run pytest -q -m stateful` then `API_TOKEN=$(openssl rand -hex 32) uv run pytest -q`
Expected: all pass, same counts as before this plan (no new 429s from the suite's own rapid calls).

- [ ] **Step 3: Run `api-tests`' contract fuzzing and perf markers once more for good measure**

Run: `cd api-tests && uv run pytest -q -m contract && uv run pytest -q -m perf`
Expected: all pass.

- [ ] **Step 4: Manually verify the `/docs` page and the docker-compose healthcheck still work**

Start the server with a token and a small rate limit (`uv run trafficflow serve-api --port 8815 --token sometoken --rate-limit 5`), `curl` `/docs` to confirm it returns `200` with a body (not blank), and run the exact `docker-compose.yml` healthcheck command by hand against a running instance with `TRAFFICFLOW_API_TOKEN` set, confirming it still returns `200` (one healthcheck call every 30s is nowhere near even a tight rate limit).

- [ ] **Step 5: Update PR #46's description and test-plan checklist**

Use the GitHub MCP tool to update PR #46 (branch `claude/determined-hamilton-q6ajd8`) title/body to describe the heavier auth+rate-limit+headers+payload-cap spec now shipped in place of the original lighter bearer-only module, and add the new verification results (Steps 1-4 above) to its test-plan checklist.

- [ ] **Step 6: Final commit if any doc/PR-description-adjacent file changed, otherwise skip**

```bash
git status
# commit only if Step 5 touched a tracked file locally; the PR body edit itself is a GitHub API call, not a commit.
```
