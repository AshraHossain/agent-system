from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from api_tests.client import ApiClient
from api_tests.config import PROJECT_ROOT, Settings
from api_tests.server import LocalServer


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture(scope="session")
def base_url(settings: Settings) -> Iterator[str]:
    """API_BASE_URL if set, otherwise a target started for this session."""
    if settings.base_url:
        yield settings.base_url
        return
    server = LocalServer(
        settings.target_project,
        log_path=PROJECT_ROOT / "reports" / "server.log",
        startup_timeout_s=settings.startup_timeout_s,
    )
    with server:
        yield server.base_url


@pytest.fixture(scope="session")
def api(base_url: str, settings: Settings) -> Iterator[ApiClient]:
    with ApiClient(base_url, timeout_s=settings.timeout_s, token=settings.token) as client:
        yield client


@pytest.fixture(scope="session")
def openapi(api: ApiClient) -> dict:
    response = api.get("/openapi.json")
    response.raise_for_status()
    return response.json()


@pytest.fixture(scope="session")
def segment_ids(api: ApiClient) -> list[str]:
    response = api.get("/segments")
    response.raise_for_status()
    ids = [s["id"] for s in response.json()]
    assert ids, "target has no segments; nothing to test"
    return ids


@pytest.fixture(scope="session")
def segment_id(segment_ids: list[str]) -> str:
    return segment_ids[0]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Append the last HTTP exchanges to a failing test's report."""
    report = yield
    if report.when == "call" and report.failed:
        client = item.funcargs.get("api")
        if isinstance(client, ApiClient) and client.history:
            lines = "\n".join(str(x) for x in list(client.history)[-5:])
            report.sections.append(("last HTTP exchanges", lines))
    return report


def pytest_report_header(config: pytest.Config) -> str:
    s = Settings.from_env()
    target = s.base_url or f"local server from {s.target_project}"
    return f"api-tests target: {target} (httpx {httpx.__version__})"
