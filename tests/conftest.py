"""Shared fixtures. All tests run offline with the deterministic mock model."""

from __future__ import annotations

from pathlib import Path

import pytest

from opspilot.core.dataset import open_dataset
from opspilot.core.telemetry import default_windows
from opspilot.core.topology import Topology
from opspilot.datasets.generate import build_all


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
