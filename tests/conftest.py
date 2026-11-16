"""Shared fixtures. All tests run offline with the deterministic mock model."""

from __future__ import annotations

from pathlib import Path

import pytest

from opspilot.core.dataset import open_dataset
from opspilot.core.telemetry import default_windows
from opspilot.core.topology import Topology
from opspilot.datasets.generate import build_all

UNIT_MODULES = frozenset(
    {
        "test_analysis",
        "test_cost",
        "test_dataset",
        "test_embeddings",
        "test_eval_repetitions",
        "test_evidence",
        "test_policy",
        "test_report",
        "test_retrieval",
        "test_security",
        "test_telemetry",
        "test_topology",
        "test_verification",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Ensure every offline test is categorized as either `unit` or `integration`."""
    for item in items:
        existing = {m.name for m in item.iter_markers()}
        if existing & {"unit", "integration", "live"}:
            continue
        mod = item.module.__name__.rsplit(".", 1)[-1]
        if mod in UNIT_MODULES:
            item.add_marker(pytest.mark.unit)
        else:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("datasets")
    build_all(d)
    return d


@pytest.fixture(scope="session")
def ds_factory(data_dir):
    def _open(case_id: str):
        return open_dataset(case_id, data_dir)

    return _open


@pytest.fixture(scope="session")
def windows():
    def _w(ds):
        return default_windows(ds.reported_at)

    return _w


@pytest.fixture(scope="session")
def topo_factory(ds_factory):
    def _t(case_id: str) -> Topology:
        return Topology.from_dataset(ds_factory(case_id))

    return _t


@pytest.fixture(scope="session")
def settings(data_dir):
    from opspilot.config import Settings

    return Settings(data_dir=data_dir)


@pytest.fixture
def run_case(settings):
    """Run a labelled case end to end in memory with its configured run faults."""
    from opspilot.adk.runner import run_investigation
    from opspilot.adk.runtime import RunFaults
    from opspilot.datasets.spec import get_case

    async def _run(
        case_id: str, *, request: str | None = None, settings_override=None, faults=None, **kw
    ):
        case = get_case(case_id)
        return await run_investigation(
            request or case["request"],
            case_id,
            settings=settings_override or settings,
            faults=faults if faults is not None else RunFaults.from_spec(case["run_faults"]),
            persist=False,
            **kw,
        )

    return _run
