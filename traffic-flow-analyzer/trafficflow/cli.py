from __future__ import annotations

import argparse
import json

from trafficflow.agents.pipeline import run_report_sync
from trafficflow.core.backtest import backtest
from trafficflow.data.feed import CsvFeed, SyntheticFeed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("trafficflow")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="print a briefing (mock model unless TRAFFICFLOW_MODEL is set)")
    d.add_argument("--horizon", type=int, default=60)
    d.add_argument("--csv", help="wide CSV of speeds instead of the simulated feed")
    d.add_argument("--json", action="store_true")
    b = sub.add_parser("backtest", help="forecaster vs persistence on the simulated feed")
    b.add_argument("--horizon", type=int, default=60)
    s = sub.add_parser("serve", help="launch the Gradio dashboard")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--host", default="127.0.0.1")
    a = p.parse_args(argv)

    if a.cmd == "demo":
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
    else:
        from trafficflow.ui.app import build_app

        build_app().launch(server_name=a.host, server_port=a.port)
    return 0
