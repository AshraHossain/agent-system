"""Assertion helpers whose failure messages carry the request and response."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, ValidationError


def _where(response: httpx.Response) -> str:
    return f"{response.request.method} {response.request.url} -> {response.status_code}"


def assert_status(response: httpx.Response, expected: int) -> None:
    assert response.status_code == expected, (
        f"{_where(response)}, expected {expected}\nbody: {response.text[:500]}"
    )


def assert_json(response: httpx.Response) -> None:
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/json"), f"{_where(response)}: content-type {ctype!r}"


def assert_schema[M: BaseModel](response: httpx.Response, model: type[M]) -> M:
    assert_json(response)
    try:
        return model.model_validate_json(response.content)
    except ValidationError as exc:
        raise AssertionError(
            f"{_where(response)} does not match {model.__name__}:\n{exc}"
        ) from None


def assert_ok[M: BaseModel](response: httpx.Response, model: type[M]) -> M:
    assert_status(response, 200)
    return assert_schema(response, model)


def assert_error[M: BaseModel](response: httpx.Response, expected: int, model: type[M]) -> M:
    assert_status(response, expected)
    return assert_schema(response, model)


def p95(samples_ms: list[float]) -> float:
    ordered = sorted(samples_ms)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
