import sqlite3

import pytest

from opspilot.core.dataset import open_dataset
from opspilot.core.errors import InvalidArgument, NotFound
from opspilot.datasets.spec import case_ids


def test_all_cases_built(data_dir):
    assert sorted(p.stem for p in data_dir.glob("*.db")) == sorted(case_ids())


def test_dataset_connection_is_read_only(ds_factory):
    ds = ds_factory("C02")
    con = ds._connect()
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM telemetry")
    con.close()


@pytest.mark.parametrize("bad", ["../etc/passwd", "C02/../C03", "", "a" * 40, "C02;DROP"])
def test_dataset_id_validation(bad, data_dir):
    with pytest.raises(InvalidArgument):
        open_dataset(bad, data_dir)


def test_missing_dataset(data_dir):
    with pytest.raises(NotFound):
        open_dataset("NOPE", data_dir)


def test_ground_truth_never_stored(ds_factory):
    """Fault specs and labels must not be observable by agents."""
    ds = ds_factory("C08")
    con = ds._connect()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    con.close()
    assert tables == {"meta", "components", "edges", "telemetry", "documents"}
    assert set(meta) == {
        "dataset_id",
        "reported_at",
        "sample_interval_min",
        "generator_version",
        "site",
    }
    blob = " ".join(str(v) for v in meta.values()) + str(ds.components) + str(ds.edges)
    for leak in ("congestion", "fault", "label", "root_cause", "world"):
        assert leak not in blob


def test_generation_is_deterministic(tmp_path, ds_factory):
    from opspilot.datasets.generate import build_case

    p = build_case("C03", tmp_path)
    a = sqlite3.connect(p).execute("SELECT SUM(value), COUNT(*) FROM telemetry").fetchone()
    b = ds_factory("C03")._rows("SELECT SUM(value), COUNT(*) FROM telemetry")[0]
    assert a == pytest.approx(b)


def test_incomplete_topology_override(ds_factory):
    edges = {(s, d) for s, d, _, _ in ds_factory("C14").edges}
    assert ("svc:video-stream", "lb-1") not in edges
    assert ("svc:video-stream", "lb-1") in {(s, d) for s, d, _, _ in ds_factory("C02").edges}
