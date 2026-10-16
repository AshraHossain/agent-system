"""Command line: ``ledger-hound api | run | score | stress | attacks``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .p2p_api.bugs import BUGS, parse_bugs

ICON = {
    "HELD": "✔ HELD    ",
    "BREACHED": "✘ BREACHED",
    "ERROR": "! ERROR   ",
    "SKIPPED": "- SKIPPED ",
}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="ledger-hound", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    api = sub.add_parser("api", help="serve the practice P2P API")
    api.add_argument("--bugs", default=None, help="comma list, 'all', or empty (see `attacks`)")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)

    run = sub.add_parser("run", help="explore + attack + judge an API, write a JSON report")
    run.add_argument("--base-url", help="API under test; omit to use the in-process practice API")
    run.add_argument("--local-bugs", default="", help="bugs for the in-process API (no --base-url)")
    run.add_argument("--mock", action="store_true", help="never call the LLM, even if a key is set")
    run.add_argument("--agent-explore", action="store_true", help="LLM drives the happy path")
    run.add_argument("--improvise", type=int, default=0, metavar="N", help="N LLM-invented probes")
    run.add_argument("--only", default="", help="comma list of attack ids")
    run.add_argument("--out", default="var/reports", help="report directory")
    run.add_argument("--quiet", action="store_true", help="print only the report path")

    sc = sub.add_parser("score", help="detection rate against every planted bug")
    sc.add_argument("--out", default="var/score.json")

    st = sub.add_parser("stress", help="N randomized POs vs an independent oracle; failure rate")
    st.add_argument("--base-url", help="API under test; omit to use the in-process practice API")
    st.add_argument("--local-bugs", default="", help="bugs for the in-process API (no --base-url)")
    st.add_argument("-n", type=int, default=50, help="number of scenarios (default 50)")
    st.add_argument("--seed", type=int, default=7, help="RNG seed; same seed = same scenarios")
    st.add_argument("--out", default="var/stress", help="output directory")

    sub.add_parser("attacks", help="list attacks and plantable bugs")

    args = parser.parse_args(argv)
    if args.cmd == "api":
        return _serve(args)
    if args.cmd == "run":
        return asyncio.run(_run(args))
    if args.cmd == "score":
        return asyncio.run(_score(args))
    if args.cmd == "stress":
        return asyncio.run(_stress(args))
    return _attacks()


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .p2p_api.app import create_app

    bugs = parse_bugs(args.bugs) if args.bugs is not None else None
    app = create_app(bugs)
    bugs_on = sorted(app.state.bugs) or "none"
    print(f"practice P2P API on http://{args.host}:{args.port}  bugs={bugs_on}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


async def _run(args: argparse.Namespace) -> int:
    from .qa.brain import make_brain
    from .qa.judge import investigate

    client, target = _target(args)
    brain = make_brain(mock=args.mock)
    try:
        report = await investigate(
            client,
            brain,
            target=target,
            agent_explore=args.agent_explore,
            improvise=args.improvise,
            only=[s for s in args.only.split(",") if s] or None,
        )
    finally:
        await client.aclose()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report-{datetime.now():%Y%m%d-%H%M%S}.json"
    text = json.dumps(report, indent=2, default=str)
    path.write_text(text)
    (out_dir / "latest.json").write_text(text)

    if args.quiet:
        print(path)
    else:
        _print_report(report, path)
    breached = report["counts"]["BREACHED"] > 0 or report["happy_path"]["status"] != "PASS"
    return 1 if breached else 0  # non-zero exit lets CI / a scheduler alert on findings


def _target(args: argparse.Namespace) -> tuple[Any, str]:
    """Client for --base-url, or for the in-process practice API with --local-bugs."""
    from .qa.http import ApiClient

    if args.base_url:
        return ApiClient.for_url(args.base_url), args.base_url
    from .p2p_api.app import create_app

    bugs = parse_bugs(args.local_bugs)
    target = f"in-process practice API (bugs: {', '.join(sorted(bugs)) or 'none'})"
    return ApiClient.for_app(create_app(bugs)), target


def _print_report(report: dict[str, Any], path: Path) -> None:
    meta = report["meta"]
    print(f"\nLedger Hound  target: {meta['target']}  brain: {meta['brain']}")
    print(f"discovery: {meta['discovery']} ({len(meta['roles'])} roles)\n")
    hp = report["happy_path"]
    print(f"HAPPY PATH ({hp['mode']}): {hp['status']}")
    for s in hp["steps"]:
        mark = "✔" if s["verdict"] == "as_expected" else "✘"
        print(f"  {mark} {s['index']:>2}. {s['name']:<28} {s['observed']}")
    print("\nADVERSARIAL")
    for r in report["adversarial"]:
        sev = f" [{r['severity']}]" if r.get("severity") else ""
        print(f"  {ICON[r['status']]} {r['attack']:<30} {r['observed'][:90]}{sev}")
    print(f"\nSUMMARY: {report['summary']}")
    for note in meta["notes"]:
        print(f"note: {note}")
    print(f"\nreport: {path}")


async def _score(args: argparse.Namespace) -> int:
    from .qa.score import score

    result = await score()
    print(
        f"\nDetection rate: {result['detection_rate']}   "
        f"false positives on clean API: {result['false_positives_on_clean_api'] or 'none'}\n"
    )
    for row in result["bugs"]:
        mark = "✔" if row["detected"] else "✘"
        extra = f"  (+{', '.join(row['also_breached'])})" if row["also_breached"] else ""
        print(
            f"  {mark} {row['bug']:<26} -> {row['caught_by']:<30} happy={row['happy_path']}{extra}"
        )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(f"\nscore: {args.out}")
    ok = result["detection_rate"].split("/")[0] == result["detection_rate"].split("/")[1]
    return 0 if ok and not result["false_positives_on_clean_api"] else 1


async def _stress(args: argparse.Namespace) -> int:
    from .qa.discovery import discover
    from .qa.stress import stress

    client, target = _target(args)
    try:
        result = await stress(client, await discover(client), n=args.n, seed=args.seed)
    finally:
        await client.aclose()
    if "error" in result:
        print(result["error"])
        return 2
    result["target"] = target

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"stress-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, indent=2, default=str))

    print(f"\nStress  target: {target}  seed: {result['seed']}")
    print(
        f"{result['failed']}/{result['scenarios']} scenarios violated a control "
        f"(failure rate {result['failure_rate']:.0%})\n"
    )
    print("  by variant:")
    for variant, row in result["by_variant"].items():
        print(f"    {variant:<20} {row['failed']:>3}/{row['scenarios']:<3} failed")
    if result["by_rule"]:
        print("  by rule (scenarios affected):")
        for rule, count in result["by_rule"].items():
            print(f"    {rule:<24} {count}")
        print("  first failures:")
        for r in [r for r in result["results"] if r["violations"]][:3]:
            v = r["violations"][0]
            print(f"    #{r['id']} {r['variant']}: [{v['rule']}] {v['detail'][:90]}")
    print(f"\nstress report: {path}")
    return 1 if result["failed"] else 0


def _attacks() -> int:
    from .qa.adversary import ATTACKS

    print("ATTACKS")
    for a in ATTACKS:
        print(f"  {a.id:<30} {a.rule:<28} [{a.severity}] {a.title}")
    print("\nPLANTABLE BUGS (practice API)")
    for b in BUGS.values():
        print(f"  {b.name:<26} caught by {b.caught_by:<30} {b.description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
