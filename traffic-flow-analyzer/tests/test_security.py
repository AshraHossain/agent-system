from trafficflow.core.auth import extract_request_token, parse_api_key
from trafficflow.core.security import SlidingWindowRateLimiter


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
