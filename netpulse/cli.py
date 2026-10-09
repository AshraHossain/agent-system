"""Command-line demo for investigations with durable human approval.

    uv run netpulse investigate --case case-04                 # runs; pauses if approval is needed
    uv run netpulse status --incident case-04                  # show state / pending review
    uv run netpulse review --incident case-04 --reviewer alice --role operator --choice approve
    uv run netpulse review --incident case-04 --reviewer bob --role senior_operator \\
        --choice request_more_investigation --comment "check the parallel uplink"

State is checkpointed to SQLite (default .netpulse/netpulse.db), so ``review`` works
from a new process. Only investigator-visible inputs are read (eval/datasets/, never
ground truth). NetPulse executes nothing; approved actions are for manual execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from netpulse.errors import ToolError
from netpulse.graph.deps import Deps
from netpulse.models import SYNTHETIC_DATA_NOTICE, ReviewInput
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService, RunStatus

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "v1" / "cases.jsonl"
DEFAULT_DB = Path(".netpulse/netpulse.db")


def _load_case(case_id: str, cases_file: Path) -> dict:
    for line in cases_file.read_text().splitlines():
        if line.strip() and (case := json.loads(line))["case_id"] == case_id:
            return case
    raise SystemExit(f"case {case_id!r} not found in {cases_file}")


def _print(status: RunStatus, state: dict) -> None:
    print(SYNTHETIC_DATA_NOTICE)
    print(f"\nIncident {status.incident_id}: status={status.status} approval={status.approval_status}")
    if status.pending_review:
        review = status.pending_review
        print(f"\nAWAITING APPROVAL (role required: {review['required_role']}; durable={status.durable})")
        print(review["summary"])
        for h in review["hypotheses"]:
            print(f"  [{h['confidence']}] {h['hypothesis_id']}: {h['rationale']}")
        for item in review["pending_actions"]:
            a, d = item["action"], item["decision"]
            print(
                f"  {a['action_id']} {a['catalog_id']} on {', '.join(a['target_entities'])} "
                f"(reversible={a['reversible']}; needs {d['required_role']}): {'; '.join(d['reasons'])}"
            )
        for reason in review["escalation_reasons"]:
            print(f"  escalation: {reason}")
        if review.get("previous_error"):
            print(f"  previous input rejected: {review['previous_error']}")
        print(f"  choices: {', '.join(review['allowed_choices'])}")
        return
    report = status.final_report or {}
    if not report:
        print("No report yet; next nodes:", status.next_nodes)
        return
    print(f"Outcome: {report['outcome']}\n{report['summary']}")
    for item in report["missing_evidence"]:
        print(f"  missing: {item}")
    for a in report["recommended_actions"]:
        print(f"  {a['kind']}: {a['catalog_id']} on {', '.join(a['target_entities'])} (executed={a['executed']})")
    for note in report["limitations"]:
        print(f"Note: {note}")
    print("\nNode trace: " + " → ".join(t["node"] for t in state.get("node_trace") or []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="netpulse", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite checkpoint + audit database")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--provider", choices=["ollama", "heuristic"], default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate", help="start one investigation")
    src = inv.add_mutually_exclusive_group(required=True)
    src.add_argument("--case", help="case id from the evaluation inputs (e.g. case-04)")
    src.add_argument("--submission", type=Path, help="JSON file with an IncidentSubmission")
    inv.add_argument("--incident-id", help="defaults to the case id")
    inv.add_argument("--submitted-at", help="ISO timestamp (required with --submission)")
    inv.add_argument("--cases-file", type=Path, default=DEFAULT_CASES)
    inv.add_argument("--json", action="store_true")

    st = sub.add_parser("status", help="show incident status or pending review")
    st.add_argument("--incident", required=True)
    st.add_argument("--json", action="store_true")

    rv = sub.add_parser("review", help="submit a reviewer decision (resumes the paused workflow)")
    rv.add_argument("--incident", required=True)
    rv.add_argument("--reviewer", required=True)
    rv.add_argument("--role", choices=["operator", "senior_operator"], required=True)
    rv.add_argument("--choice", choices=["approve", "reject", "request_more_investigation"], required=True)
    rv.add_argument("--actions", help="comma-separated action ids to approve (default: all pending)")
    rv.add_argument("--comment", default="")
    rv.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    store = PersistentStore(args.db)
    try:
        return _run(parser, args, store)
    finally:
        store.close()


def _run(parser: argparse.ArgumentParser, args: argparse.Namespace, store: PersistentStore) -> int:
    try:
        service = InvestigationService(Deps.default(args.data_dir, provider=args.provider), store)
        if args.command == "investigate":
            if args.case:
                case = _load_case(args.case, args.cases_file)
                submission, submitted_at, incident_id = case["submission"], case["submitted_at"], case["case_id"]
            else:
                if not args.submitted_at:
                    parser.error("--submitted-at is required with --submission")
                submission, submitted_at = json.loads(args.submission.read_text()), args.submitted_at
                incident_id = args.incident_id or f"inc-{args.submission.stem}"
            status = service.start(submission, submitted_at=submitted_at, incident_id=args.incident_id or incident_id)
        elif args.command == "status":
            status = service.status(args.incident)
        else:
            review = ReviewInput(
                reviewer_id=args.reviewer,
                reviewer_role=args.role,
                choice=args.choice,
                comment=args.comment,
                approved_action_ids=args.actions.split(",") if args.actions else None,
            )
            status = service.decide(args.incident, review)
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    if args.json:
        json.dump(status.model_dump(mode="json"), sys.stdout, indent=2)
        print()
    else:
        _print(status, service.state(status.incident_id))
    return 2 if status.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
