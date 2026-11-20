from trafficflow.core.auth import extract_request_token, parse_api_key


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
