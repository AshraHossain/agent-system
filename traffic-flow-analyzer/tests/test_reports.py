import gc
import sqlite3
import warnings

import pytest

from trafficflow.agents.pipeline import run_report_sync
from trafficflow.data.feed import SyntheticFeed
from trafficflow.reports import ReportStore


def test_report_store_round_trip_and_persistence(tmp_path):
    path = tmp_path / "reports.db"
    feed = SyntheticFeed()
    feed.advance(12)
    report = run_report_sync(feed)
    with ReportStore(path) as first:
        label = first.save(report, "summary text")
    assert report.status.value in label

    with ReportStore(path) as reopened:  # survives a restart
        rows = reopened.recent()
        assert [r["label"] for r in rows] == [label]
        assert reopened.summary(rows[0]["id"]) == "summary text"

        reopened.clear()
        assert reopened.recent() == []


def test_report_store_recent_is_bounded_and_ordered():
    report = run_report_sync(SyntheticFeed())
    with ReportStore() as store:
        for i in range(12):
            store.save(report, f"s{i}")
        rows = store.recent(10)
        assert len(rows) == 10
        assert [store.summary(r["id"]) for r in rows] == [f"s{i}" for i in range(2, 12)]


def test_closing_a_store_leaves_no_unclosed_connection(tmp_path):
    """A store that is closed emits no ResourceWarning when collected.

    Regression guard: ReportStore holds one long-lived connection (shared by Gradio's
    worker threads), and before it had a close() an abandoned store raised
    "ResourceWarning: unclosed database" whenever the collector got to it.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        with ReportStore(tmp_path / "r.db"):
            pass
        gc.collect()


def test_close_is_idempotent_and_blocks_further_use(tmp_path):
    store = ReportStore(tmp_path / "r.db")
    store.close()
    store.close()  # must not raise
    with pytest.raises(sqlite3.ProgrammingError):
        store.recent()
