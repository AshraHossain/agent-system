"""Command-line demo: run an investigation on an evaluation case input or a submission file.

    uv run netpulse investigate --case case-04
    uv run netpulse investigate --submission my_incident.json --submitted-at 2026-03-09T14:00:00Z --json

Only investigator-visible inputs are read (eval/datasets/, never ground truth).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from netpulse.graph.deps import Deps
from netpulse.graph.runner import initial_state, run_investigation
from netpulse.models import SYNTHETIC_DATA_NOTICE

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "v1" / "cases.jsonl"


def _load_case(case_id: str, cases_file: Path) -> dict:
    for line in cases_file.read_text().splitlines():
        if line.strip() and (case := json.loads(line))["case_id"] == case_id:
            return case
    raise SystemExit(f"case {case_id!r} not found in {cases_file}")


def _print_summary(state: dict) -> None:
    report = state["final_report"]
    hyps = {h["hypothesis_id"]: h for h in state.get("hypotheses") or []}
    print(SYNTHETIC_DATA_NOTICE)
    print(f"\nIncident {report['incident_id']}: {report['outcome']}")
    print(report["summary"])
    for r in report["top_hypotheses"]:
        h = hyps.get(r["hypothesis_id"], {})
        print(f"  {r['rank']}. [{r['confidence']}] {h.get('cause_category')} at {h.get('suspected_root_entity')}")
        print(f"     {r['rationale']}")
    if report["missing_evidence"]:
        print("Missing evidence:\n  - " + "\n  - ".join(report["missing_evidence"]))
    if report["recommended_actions"]:
        print("Recommended diagnostics (read-only, not executed):")
        for a in report["recommended_actions"]:
            print(f"  - {a['catalog_id']} on {', '.join(a['target_entities'])}")
    for note in report["limitations"]:
        print(f"Note: {note}")
    print("\nNode trace: " + " → ".join(t["node"] for t in state.get("node_trace") or []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="netpulse", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("investigate", help="run one investigation")
    src = inv.add_mutually_exclusive_group(required=True)
    src.add_argument("--case", help="case id from the evaluation inputs (e.g. case-04)")
    src.add_argument("--submission", type=Path, help="JSON file with an IncidentSubmission")
    inv.add_argument("--submitted-at", help="ISO timestamp (required with --submission)")
    inv.add_argument("--cases-file", type=Path, default=DEFAULT_CASES)
    inv.add_argument("--data-dir", type=Path, default=None)
    inv.add_argument("--json", action="store_true", help="print the final report as JSON")
    inv.add_argument(
        "--provider",
        choices=["ollama", "heuristic"],
        default=None,
        help="hypothesis generator (default: NETPULSE_LLM_PROVIDER, else ollama with visible fallback)",
    )
    args = parser.parse_args(argv)

    if args.case:
        case = _load_case(args.case, args.cases_file)
        submission, submitted_at, incident_id = case["submission"], case["submitted_at"], case["case_id"]
    else:
        if not args.submitted_at:
            parser.error("--submitted-at is required with --submission")
        submission, submitted_at, incident_id = json.loads(args.submission.read_text()), args.submitted_at, None

    deps = Deps.default(args.data_dir, provider=args.provider)
    state = run_investigation(
        deps, initial_state(submission, submitted_at=submitted_at, incident_id=incident_id, source="api", deps=deps)
    )
    if args.json:
        json.dump(state["final_report"], sys.stdout, indent=2)
        print()
    else:
        _print_summary(state)
    return 0 if state["final_report"]["outcome"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
