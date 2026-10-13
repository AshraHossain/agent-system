"""Command-line tool for investigations and for operating the database.

    uv run netpulse investigate --case case-04                 # runs locally; pauses if approval is needed
    uv run netpulse status --incident case-04                  # show state / pending review

    # Reviewing goes through the API, so your identity and role come from YOUR validated token:
    export NETPULSE_API_URL=https://api.example.com NETPULSE_API_TOKEN=...   # an OIDC access token in production
    uv run netpulse review --incident case-04 --choice approve --wait

    # Development only: write a decision straight to the database with a self-asserted identity.
    NETPULSE_ALLOW_LOCAL_REVIEW=true uv run netpulse review --local --incident case-04 \\
        --reviewer alice --role operator --choice approve

``investigate``, ``status``, ``audit-verify``, ``backup`` and ``prune`` open the database file directly. They are
administrator tools: whoever can run them already holds the file and the audit key. ``review`` never needs the file.
State is checkpointed to SQLite (default .netpulse/netpulse.db). Only investigator-visible inputs are read
(eval/datasets/, never ground truth). NetPulse executes nothing; approved actions are for manual execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from netpulse.errors import ToolError
from netpulse.graph.deps import Deps
from netpulse.models import SYNTHETIC_DATA_NOTICE, ReviewInput
from netpulse.observability import apply_tracing_policy, configure_logging
from netpulse.persistence.store import PersistentStore
from netpulse.service import InvestigationService, RunStatus
from netpulse.ui.client import ApiClient, ApiError

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

    rv = sub.add_parser("review", help="submit a reviewer decision through the API, as the identity your token proves")
    rv.add_argument("--incident", required=True)
    rv.add_argument("--choice", choices=["approve", "reject", "request_more_investigation"], required=True)
    rv.add_argument("--actions", help="comma-separated action ids to approve (default: all pending)")
    rv.add_argument("--comment", default="")
    rv.add_argument("--json", action="store_true")
    rv.add_argument("--api-url", default=None, help="API base URL (or NETPULSE_API_URL); https unless localhost")
    rv.add_argument("--wait", action="store_true", help="wait for the workflow to finish after the decision")
    rv.add_argument("--local", action="store_true", help="DEVELOPMENT ONLY: bypass the API, self-asserted identity")
    rv.add_argument("--reviewer", help="with --local only: the reviewer name to record (unverified)")
    rv.add_argument("--role", choices=["operator", "senior_operator"], help="with --local only (unverified)")

    av = sub.add_parser("audit-verify", help="verify the audit log hash chain (exit 1 if it was tampered with)")
    av.add_argument("--expect-head", help="recorded head <seq>:<hash>; also detects truncation of the newest rows")

    bk = sub.add_parser("backup", help="write a consistent online snapshot of the database and verify its audit chain")
    bk.add_argument("--to", type=Path, required=True, help="destination file (must not exist)")

    pr = sub.add_parser("prune", help="delete checkpoints and job records of incidents finished before a cutoff")
    pr.add_argument("--older-than-days", type=float, required=True)
    pr.add_argument("--dry-run", action="store_true", help="list what would be removed without removing it")

    args = parser.parse_args(argv)
    configure_logging(level=os.environ.get("NETPULSE_LOG_LEVEL", "WARNING"))
    apply_tracing_policy()
    if args.command == "review":
        problem = _review_mode_problem(args)
        if problem:
            parser.error(problem)
        if not args.local:
            return _review_via_api(args)
    try:
        store = PersistentStore(args.db)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        if args.command == "audit-verify":
            return _audit_verify(store, args.expect_head)
        if args.command == "backup":
            return _backup(store, args.to)
        if args.command == "prune":
            return _prune(store, args.older_than_days, args.dry_run)
        return _run(parser, args, store)
    finally:
        store.close()


LOCAL_REVIEW_ENV = "NETPULSE_ALLOW_LOCAL_REVIEW"


def _review_mode_problem(args: argparse.Namespace) -> str | None:
    """Why the arguments are not a valid review request, or None."""
    if not args.local:
        if args.reviewer or args.role:
            return (
                "--reviewer and --role are only valid with --local. A normal review takes your identity and role "
                "from your API token, so there is nothing to claim on the command line."
            )
        return None
    if os.environ.get(LOCAL_REVIEW_ENV, "").strip().lower() not in {"1", "true", "yes", "on"}:
        return (
            f"--local writes a decision with a self-asserted identity and is disabled unless {LOCAL_REVIEW_ENV}=true. "
            "Use the API-backed review (set NETPULSE_API_URL and NETPULSE_API_TOKEN) for anything real."
        )
    if not args.reviewer or not args.role:
        return "--local needs both --reviewer and --role"
    return None


def _api_client(url: str, token: str) -> ApiClient:
    return ApiClient(url, token)


def _check_api_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        return None
    return f"refusing to send a token to {url!r}: the API URL must be https (plain http only for localhost)"


def _review_via_api(args: argparse.Namespace) -> int:
    """Submit through the API: the server learns who we are from the token and enforces role and four-eyes."""
    url = args.api_url or os.environ.get("NETPULSE_API_URL", "")
    token = os.environ.get("NETPULSE_API_TOKEN", "")
    if not url:
        print("error: set NETPULSE_API_URL (or pass --api-url)", file=sys.stderr)
        return 2
    if not token:
        print(
            "error: set NETPULSE_API_TOKEN to your API access token (from the environment, never an argument)",
            file=sys.stderr,
        )
        return 2
    if problem := _check_api_url(url):
        print(f"error: {problem}", file=sys.stderr)
        return 2
    client = _api_client(url, token)
    try:
        who = client.whoami()
        accepted = client.decide(
            args.incident,
            args.choice,
            args.comment,
            args.actions.split(",") if args.actions else None,
        )
        status = _wait_for_workflow(client, args.incident) if args.wait else client.status(args.incident)
    except ApiError as exc:
        print(f"error: {exc.detail}", file=sys.stderr)
        return 3
    if args.json:
        json.dump({"reviewer": who, "accepted": accepted, "status": status}, sys.stdout, indent=2)
        print()
    else:
        print(SYNTHETIC_DATA_NOTICE)
        print(f"\nDecision '{args.choice}' submitted as {who['name']} ({who['role']}, tenant {who['tenant']}).")
        print(
            f"Incident {args.incident}: workflow={status.get('status')} "
            f"approval={status.get('approval_status')} job={status.get('job_state')}"
        )
        if status.get("error"):
            print(f"job error: {status['error']}")
    return 2 if status.get("status") == "failed" else 0


def _wait_for_workflow(client: ApiClient, incident: str, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = client.status(incident)
        if status.get("job_state") in {"done", "error"} and status.get("status") != "running":
            return status
        if time.monotonic() > deadline:
            return status
        time.sleep(0.5)


def _audit_verify(store: PersistentStore, expected_head: str | None) -> int:
    result = store.verify_audit(expected_head)
    if not result.keyed:
        print("warning: NETPULSE_AUDIT_KEY is not set; an unkeyed chain cannot stop someone who can rewrite the file")
    status = "OK" if result.ok else f"FAILED: {result.error}"
    print(f"audit chain {status} ({result.rows} rows, {result.legacy_rows} legacy unchained)")
    print(f"head: {result.head or 'none'}")
    return 0 if result.ok else 1


def _backup(store: PersistentStore, destination: Path) -> int:
    try:
        result = store.backup(destination)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    status = "audit chain verified in the copy" if result.ok else f"AUDIT CHAIN FAILED in the copy: {result.error}"
    print(f"backup written to {destination} ({result.rows} audit rows); {status}")
    return 0 if result.ok else 1


def _prune(store: PersistentStore, days: float, dry_run: bool) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    pruned = store.prune(cutoff, dry_run=dry_run)
    verb = "would remove" if dry_run else "removed"
    print(f"{verb} checkpoints and job records for {len(pruned)} incident(s) finished before {cutoff:%Y-%m-%d %H:%M}Z")
    for key in pruned:
        print(f"  {key}")
    print("the audit log is never pruned (its hash chain must stay contiguous)")
    return 0


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
            status = service.decide(args.incident, review, channel="local-cli")
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
