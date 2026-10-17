from __future__ import annotations

import argparse
import json
import os
import sys

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
    eg = e.add_mutually_exclusive_group()
    eg.add_argument("--sensors", type=int, default=38, help="number of evenly-spaced sensors")
    eg.add_argument("--all-sensors", action="store_true", help="load all 228 sensors")
    e.add_argument("--horizons", type=int, nargs="+", default=[15, 30, 60])
    e.add_argument("--from-hour", type=int, default=6)
    e.add_argument("--to-hour", type=int, default=20)
    e.add_argument("--train-window", type=int, default=0, help="history points per origin (0=all)")
    e.add_argument(
        "--period",
        choices=["weekday", "all"],
        default="weekday",
        help="'weekday' skips weekend gaps (default); 'all' is identical for PeMSD7 "
        "(no weekend data) but is wired for future feeds that include weekends",
    )
    v = sub.add_parser(
        "validate", help="run pipeline with a real model and print the report as JSON"
    )
    v.add_argument(
        "--model", default=None, help="pydantic-ai model string (overrides TRAFFICFLOW_MODEL)"
    )
    v.add_argument("--segment", default="S1", help="segment to focus on (default: S1)")
    v.add_argument(
        "--horizon", type=int, default=60, help="forecast horizon in minutes (default: 60)"
    )
    re = sub.add_parser("run-eval", help="run the labels-hidden agent evaluation cases")
    re.add_argument(
        "--cases",
        default=None,
        help="path to cases.yaml (default: trafficflow/eval/cases.yaml)",
    )
    re.add_argument("--verbose", action="store_true")
    s = sub.add_parser("serve", help="launch the Gradio dashboard")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--host", default="127.0.0.1")
    sg = s.add_mutually_exclusive_group()
    sg.add_argument("--replay", help="PeMSD7_V_228.csv: replay real recorded speeds")
    sg.add_argument("--csv", help="wide CSV of speeds (timestamp + one column per segment)")
    s.add_argument("--sensors", type=int, default=8, help="sensors shown when replaying")
    s.add_argument(
        "--poll-seconds",
        type=int,
        default=0,
        help="re-read --csv from disk every N seconds (0=disabled)",
    )
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
    elif a.cmd == "validate":
        model = a.model or os.environ.get("TRAFFICFLOW_MODEL")
        if not model or model == "mock":
            print(
                "ERROR: set TRAFFICFLOW_MODEL or pass --model <provider:model-id>.\n"
                "Example: TRAFFICFLOW_MODEL=google-gla:gemini-2.0-flash "
                "uv run trafficflow validate",
                file=sys.stderr,
            )
            return 1
        feed = SyntheticFeed()
        feed.advance(12)  # 08:30 — inside the morning incident window
        r = run_report_sync(feed, a.horizon, model=model)
        print(r.model_dump_json(indent=2))
        hard_fail = any(i.startswith("HARD_FAIL") for i in r.issues)
        if r.issues:
            print("\n".join(r.issues), file=sys.stderr)
        return 1 if hard_fail else 0
    elif a.cmd == "run-eval":
        from trafficflow.eval.runner import run_cases

        path = a.cases or None
        summary = (
            run_cases(cases_path=path, verbose=a.verbose) if path else run_cases(verbose=a.verbose)
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["failed"] == 0 else 1
    elif a.cmd == "evaluate":
        n_sensors = None if a.all_sensors else a.sensors
        feed = ReplayFeed(load_pemsd7(a.path, sensors=n_sensors))
        times = [f"{h:02d}:00" for h in range(a.from_hour, a.to_hour + 1)]
        origins = feed.origins(times, max_steps=max(a.horizons) // 5)
        tw = a.train_window or None
        print(json.dumps(evaluate(feed, origins, a.horizons, train_window=tw), indent=2))
    else:
        from trafficflow.ui.app import build_app

        factory = None
        if a.replay:
            data = load_pemsd7(a.replay, sensors=a.sensors)

            def factory() -> ReplayFeed:
                return ReplayFeed(data, start=data.index[8 * 288])

        elif a.csv:
            csv_path = a.csv

            def factory() -> CsvFeed:  # re-reads disk on each call when poll-seconds > 0
                return CsvFeed(csv_path)

        build_app(feed_factory=factory).launch(server_name=a.host, server_port=a.port)
    return 0
