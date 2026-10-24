from trafficflow.agents.pipeline import run_report_sync
from trafficflow.data.feed import SyntheticFeed
from trafficflow.reports import ReportStore


def test_report_store_round_trip_and_persistence(tmp_path):
    path = tmp_path / "reports.db"
    feed = SyntheticFeed()
    feed.advance(12)
    report = run_report_sync(feed)
    label = ReportStore(path).save(report, "summary text")
    assert report.status.value in label

    reopened = ReportStore(path)  # survives a restart
    rows = reopened.recent()
    assert [r["label"] for r in rows] == [label]
    assert reopened.summary(rows[0]["id"]) == "summary text"

    reopened.clear()
    assert reopened.recent() == []


def test_report_store_recent_is_bounded_and_ordered():
    store = ReportStore()
    report = run_report_sync(SyntheticFeed())
    for i in range(12):
        store.save(report, f"s{i}")
    rows = store.recent(10)
    assert len(rows) == 10
    assert [store.summary(r["id"]) for r in rows] == [f"s{i}" for i in range(2, 12)]
