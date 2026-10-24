import io
import json
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trafficflow.cli import main
from trafficflow.core.evaluation import (
    evaluate,
    historical_average,
    persistence,
    seasonal_naive,
    select_period,
)
from trafficflow.core.forecast import forecast_segment
from trafficflow.data import replay
from trafficflow.data.replay import (
    MPH_TO_KMH,
    ReplayFeed,
    fetch_pems08,
    fetch_pemsd7,
    load_pems08,
    load_pemsd7,
    load_replay,
)
from trafficflow.ui.app import build_app


def write_pems_like(path, days=10, sensors=4, seed=0):
    """Headerless file in the PeMSD7 layout: days*288 rows, one mph column per sensor."""
    rng = np.random.default_rng(seed)
    h = np.tile(np.arange(288) / 12, days)
    dip = 25 * np.exp(-0.5 * ((h - 8) / 1.2) ** 2) + 20 * np.exp(-0.5 * ((h - 17.5) / 1.5) ** 2)
    cols = [65 - dip * (0.6 + 0.2 * k) + rng.normal(0, 1.0, len(h)) for k in range(sensors)]
    np.savetxt(path, np.column_stack(cols), delimiter=",", fmt="%.4f")
    return path


@pytest.fixture
def pems(tmp_path):
    return write_pems_like(tmp_path / "PeMSD7_V_228.csv")


def test_loader_reconstructs_weekday_timestamps_and_converts_units(pems):
    df = load_pemsd7(pems)
    raw = pd.read_csv(pems, header=None)
    assert df.shape == (10 * 288, 4)
    assert df.index[0] == pd.Timestamp("2012-05-01 00:00")
    assert df.index[288] == pd.Timestamp("2012-05-02 00:00")
    assert set(df.index.dayofweek) <= {0, 1, 2, 3, 4}  # weekday-only
    assert df.index[5 * 288] == pd.Timestamp("2012-05-08 00:00")  # skips the weekend
    assert df.iloc[0, 0] == pytest.approx(raw.iloc[0, 0] * MPH_TO_KMH)
    assert list(df.columns) == ["P000", "P001", "P002", "P003"]


def test_loader_sensor_subset_and_bad_shape(pems, tmp_path):
    assert list(load_pemsd7(pems, sensors=2).columns) == ["P000", "P003"]
    bad = tmp_path / "bad.csv"
    bad.write_text("1,2\n3,4\n")
    with pytest.raises(ValueError, match="whole number"):
        load_pemsd7(bad)


def test_replay_feed_history_truth_and_weekend_skip(pems):
    feed = ReplayFeed(load_pemsd7(pems))
    assert feed.now == datetime(2012, 5, 10, 0, 0)  # first day after the 7-day warm-up
    assert len(feed.history("P000", days=7)) == 7 * 288
    nxt = feed.next_values("P000", 3)
    assert len(nxt) == 3
    feed.now = datetime(2012, 5, 11, 23, 55)  # Friday evening
    assert feed.advance(1) == datetime(2012, 5, 14, 0, 0)  # Monday: weekend is skipped
    assert all(s.free_flow_kmh > 0 for s in feed.segments())


def test_replay_feed_requires_warmup():
    df = pd.DataFrame(
        {"P000": np.ones(3 * 288)}, index=pd.date_range("2012-05-01", periods=3 * 288, freq="5min")
    )
    with pytest.raises(ValueError, match="more than 7"):
        ReplayFeed(df)


def test_origins_never_cross_a_gap(pems):
    feed = ReplayFeed(load_pemsd7(pems))
    origins = feed.origins(["23:00", "08:00"], max_steps=12)
    assert origins and all(o >= datetime(2012, 5, 10) for o in origins)
    for o in origins:
        feed.now = o
        assert len(feed.next_values("P000", 12)) == 12
        assert feed.index[feed.index.get_loc(pd.Timestamp(o)) + 12] - pd.Timestamp(
            o
        ) == pd.Timedelta(minutes=60)


def test_forecast_survives_horizon_crossing_the_weekend_gap(pems):
    feed = ReplayFeed(load_pemsd7(pems))
    feed.now = datetime(2012, 5, 11, 23, 30)  # Friday 23:30; horizon reaches Saturday
    seg = feed.segments()[0]
    fc = forecast_segment(feed.history(seg.id), seg, 60)
    assert not any(np.isnan(p.speed_kmh) for p in fc.points)


def test_baselines_shapes_and_values(pems):
    hist = ReplayFeed(load_pemsd7(pems)).history("P000")
    for fn in (persistence, seasonal_naive, historical_average):
        out = fn(hist, 12)
        assert out.shape == (12,) and not np.isnan(out).any()
    assert persistence(hist, 3).tolist() == [float(hist.iloc[-1])] * 3


def test_evaluate_reports_baselines_coverage_and_restores_clock(pems):
    feed = ReplayFeed(load_pemsd7(pems))
    start = feed.now
    res = evaluate(feed, feed.origins(["07:00", "08:00", "12:00"], 12), horizons=(15, 60))
    assert feed.now == start
    row = res["results"]["60min/all"]
    assert {"model", "persistence", "seasonal_naive", "historical_average"} <= set(row)
    assert 0 <= row["interval_90"]["coverage"] <= 1
    assert row["model"]["n"] == row["persistence"]["n"] > 0
    assert "60min/peak" in res["results"] and "60min/offpeak" in res["results"]


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_checks_hash_and_extracts_only_the_named_member(tmp_path, monkeypatch):
    blob = zip_bytes({replay.PEMSD7_MEMBER: "1,2\n", "../evil.txt": "x", "PeMSD7_W_228.csv": "9"})
    monkeypatch.setattr(replay, "urlopen", lambda url, timeout: _Resp(blob))
    monkeypatch.setattr(replay, "PEMSD7_ZIP_SHA256", __import__("hashlib").sha256(blob).hexdigest())
    out = fetch_pemsd7(tmp_path / "d")
    assert out.read_text() == "1,2\n"
    assert [p.name for p in (tmp_path / "d").iterdir()] == [replay.PEMSD7_MEMBER]
    assert not (tmp_path / "evil.txt").exists()


def test_fetch_rejects_wrong_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "urlopen", lambda url, timeout: _Resp(zip_bytes({"a": "b"})))
    with pytest.raises(ValueError, match="hash"):
        fetch_pemsd7(tmp_path / "d")
    assert not (tmp_path / "d" / replay.PEMSD7_MEMBER).exists()


def test_dashboard_builds_with_replay_feed(pems):
    data = load_pemsd7(pems)
    assert build_app(feed_factory=lambda: ReplayFeed(data)) is not None


def test_cli_evaluate_on_replay(pems, capsys):
    assert (
        main(["evaluate", str(pems), "--sensors", "2", "--from-hour", "7", "--to-hour", "8"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["segments"] == 2 and "15min/all" in out["results"]


def test_cli_demo_replay(pems, capsys):
    assert main(["demo", "--replay", str(pems)]) == 0
    assert capsys.readouterr().out.startswith("[")


def write_pems08_like(path, days=10, sensors=3, seed=0):
    """PEMS08 layout: (days*288, sensors, [flow, occupancy, speed mph]) in an .npz."""
    rng = np.random.default_rng(seed)
    h = np.tile(np.arange(288) / 12, days)
    dip = 25 * np.exp(-0.5 * ((h - 8) / 1.2) ** 2)
    speed = np.column_stack(
        [65 - dip * (0.6 + 0.2 * k) + rng.normal(0, 1, len(h)) for k in range(sensors)]
    )
    data = np.stack([speed * 3, speed / 1000, speed], axis=2)
    np.savez(path, data=data)
    return path


@pytest.fixture
def pems08(tmp_path):
    return write_pems08_like(tmp_path / "pems08.npz")


def test_load_pems08_keeps_weekends_and_converts_speed(pems08):
    raw = np.load(pems08)["data"][:, :, 2]
    df = load_pems08(pems08)
    assert df.attrs["source"] == "PEMS08"
    assert df.index[0] == pd.Timestamp("2016-07-01") and df.index.freq == "5min"
    assert (df.index.dayofweek >= 5).any()  # 2016-07-02 is a Saturday
    np.testing.assert_allclose(df["P000"].to_numpy(), raw[:, 0] * MPH_TO_KMH)
    assert list(load_pems08(pems08, sensors=2).columns) == ["P000", "P002"]
    assert load_replay(pems08).attrs["source"] == "PEMS08"


def test_replay_feed_on_pems08_scores_weekend_origins(pems08):
    feed = ReplayFeed(load_pems08(pems08))
    assert feed.segments()[0].name.endswith("(PEMS08)")
    origins = feed.origins(["08:00"], max_steps=12)
    weekend = select_period(origins, "weekend")
    assert weekend and all(o.weekday() >= 5 for o in weekend)
    assert len(select_period(origins, "weekday")) + len(weekend) == len(origins)
    assert select_period(origins, "all") == origins
    with pytest.raises(ValueError):
        select_period(origins, "holidays")


def test_fetch_pems08_checks_hash(tmp_path, monkeypatch):
    blob = b"npz-bytes"
    monkeypatch.setattr(replay, "urlopen", lambda url, timeout: _Resp(blob))
    with pytest.raises(ValueError, match="hash"):
        fetch_pems08(tmp_path / "d")
    assert not (tmp_path / "d" / replay.PEMS08_FILE).exists()
    monkeypatch.setattr(replay, "PEMS08_SHA256", __import__("hashlib").sha256(blob).hexdigest())
    assert fetch_pems08(tmp_path / "d").read_bytes() == blob


def test_cli_evaluate_pems08_weekend(pems08, capsys):
    args = ["evaluate", str(pems08), "--sensors", "2", "--from-hour", "8", "--to-hour", "8"]
    assert main([*args, "--period", "weekend"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["origins"] > 0 and out["segments"] == 2
