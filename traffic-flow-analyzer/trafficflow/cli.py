from __future__ import annotations

import argparse
import json

from trafficflow.agents.pipeline import run_report_sync
from trafficflow.core.backtest import backtest
from trafficflow.core.evaluation import evaluate
from trafficflow.data.feed import CsvFeed, SyntheticFeed
from trafficflow.data.replay import ReplayFeed, fetch_pemsd7, load_pemsd7


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("trafficflow")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="print a briefing (mock model unless TRAFFICFLOW_MODEL is set)")
    d.add_argument("--horizon", type=int, default=60)
    d.add_argument("--csv", help="wide CSV of speeds instead of the simulated feed")
    d.add_argument("--replay", help="PeMSD7_V_228.csv: replay real recorded speeds")
    d.add_argument("--json", action="store_true")
    b = sub.add_parser("backtest", help="forecaster vs persistence on the simulated feed")
    b.add_argument("--horizon", type=int, default=60)
    f = sub.add_parser("fetch-data", help="download the public PeMSD7 speed file (SHA-256 checked)")
    f.add_argument("--dest", default="var/data")
    e = sub.add_parser("evaluate", help="full evaluation on replayed PeMSD7 data")
    e.add_argument("path", help="PeMSD7_V_228.csv (see fetch-data)")
    e.add_argument("--sensors", type=int, default=38)
    e.add_argument("--horizons", type=int, nargs="+", default=[15, 30, 60])
    e.add_argument("--from-hour", type=int, default=6)
    e.add_argument("--to-hour", type=int, default=20)
    s = sub.add_parser("serve", help="launch the Gradio dashboard")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--replay", help="PeMSD7_V_228.csv: replay real recorded speeds")
    s.add_argument("--sensors", type=int, default=8, help="sensors shown when replaying")
    a = p.parse_args(argv)

    if a.cmd == "demo":
        if a.replay:
            feed = ReplayFeed(load_pemsd7(a.replay, sensors=8))
            feed.advance(96)  # first origin is 00:00; move to 08:00
        else:
            feed = CsvFeed(a.csv) if a.csv else SyntheticFeed()
            if not a.csv:
                feed.advance(12)  # 08:30, inside the morning incident
        r = run_report_sync(feed, a.horizon)
        print(
            r.model_dump_json(indent=2)
            if a.json
            else f"[{r.status.value.upper()}] {r.brief.headline}\n{r.brief.summary}\n"
            + "\n".join(f"- {x}" for x in r.brief.recommendations)
            + (f"\nISSUES: {r.issues}" if r.issues else "")
        )
    elif a.cmd == "backtest":
        print(json.dumps(backtest(SyntheticFeed(), a.horizon), indent=2))
    elif a.cmd == "fetch-data":
        print(fetch_pemsd7(a.dest))
    elif a.cmd == "evaluate":
        feed = ReplayFeed(load_pemsd7(a.path, sensors=a.sensors))
        times = [f"{h:02d}:00" for h in range(a.from_hour, a.to_hour + 1)]
        origins = feed.origins(times, max_steps=max(a.horizons) // 5)
        print(json.dumps(evaluate(feed, origins, a.horizons), indent=2))
    else:
        from trafficflow.ui.app import build_app

        factory = None
        if a.replay:
            data = load_pemsd7(a.replay, sensors=a.sensors)

            def factory() -> ReplayFeed:
                return ReplayFeed(data, start=data.index[8 * 288])

        build_app(feed_factory=factory).launch(server_name=a.host, server_port=a.port)
    return 0
