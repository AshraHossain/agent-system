"""`opspilot` command line: build data, run the demo, investigate, resume, inspect, evaluate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

from opspilot.config import REPO_ROOT, Settings
from opspilot.observability import configure_logging

W = 100


def _h(title: str) -> None:
    print(f"\n{'=' * W}\n{title}\n{'=' * W}")


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, W, initial_indent=indent, subsequent_indent=indent + "  ")


def _ensure_data(settings: Settings, case_ids: list[str] | None = None) -> None:
    from opspilot.datasets import generate, spec

    ids = case_ids or spec.case_ids()
    missing = [c for c in ids if not (settings.data_dir / f"{c}.db").exists()]
    if missing:
        print(f"building synthetic datasets: {', '.join(missing)}")
        generate.build_all(settings.data_dir, missing)


def print_outcome(out, *, case: dict | None = None, show_labels: bool = False) -> None:
    r, st = out.report, out.state
    _h("2. AGENT PIPELINE (ADK events)")
    for step in out.trace:
        if step.tool_calls:
            print(f"  [{step.author}] tools: {', '.join(step.tool_calls)}")
        elif step.author in ("intake", "evidence_verifier", "finalizer"):
            print(_wrap(f"[{step.author}] {step.text}"))
    _h("3. SPECIALIST FINDINGS")
    for key in ("telemetry_finding", "topology_finding", "knowledge_finding"):
        f = st.get(key) or {}
        print(f"\n  {key} — status: {f.get('status')}")
        print(_wrap(f.get("summary", ""), "    "))
        if key == "telemetry_finding":
            for a in f.get("anomalies", [])[:8]:
                print(_wrap(f"• {a['entity_id']} {a['description']} {a['evidence_ids']}", "    "))
            for g in f.get("data_gaps", []):
                print(_wrap(f"! gap: {g}", "    "))
        if key == "topology_finding":
            for b in f.get("blast_radius", [])[:3]:
                imp = ", ".join(f"{i['service']}={i['impact']}" for i in b["impacts"])
                print(_wrap(f"• blast radius {b['component_id']}: {imp}", "    "))
            for u in f.get("uncertainties", [])[:4]:
                print(_wrap(f"? {u}", "    "))
        if key == "knowledge_finding":
            for d in f.get("relevant_runbooks", []):
                print(_wrap(f"• runbook {d['doc_id']}: {d['title']}", "    "))
            for d in f.get("outdated_or_conflicting", []):
                print(_wrap(f"! outdated {d['doc_id']}: {d['issue']}", "    "))
            for d in f.get("suspicious_documents", []):
                print(_wrap(f"!! quarantined {d['doc_id']}: {d['issue']}", "    "))
    print("\n  incident_analysis — hypotheses (ranked; hypotheses, not facts):")
    for h in r.root_cause_hypotheses[:4]:
        print(
            _wrap(
                f"{h.hypothesis_id} [{h.confidence}] {h.category.value} @ {h.component_id}: "
                f"{h.statement}",
                "    ",
            )
        )
        print(
            _wrap(
                f"supporting {h.supporting_evidence_ids}; contradicting "
                f"{h.contradicting_evidence_ids}; similar past incidents "
                f"{h.historical_references}",
                "      ",
            )
        )
    _h("4. EVIDENCE AND UNRESOLVED QUESTIONS")
    for e in r.supporting_evidence[:12]:
        print(_wrap(f"{e.evidence_id} ({e.kind}{'' if e.trusted else ', untrusted'}): {e.summary}"))
    for e in r.contradicting_evidence:
        print(_wrap(f"CONTRA {e.evidence_id}: {e.summary}"))
    for q in r.unresolved_questions[:8]:
        print(_wrap(f"? {q}"))
    _h("5. VERIFICATION")
    v = r.verification
    if v:
        print(f"  verdict: {v.verdict}; claims checked: {v.claims_checked}")
        for c in v.checks:
            print(f"  {'PASS' if c.passed else 'FAIL'} {c.name}: {c.details}")
        for i in v.issues:
            print(_wrap(f"{i.severity.upper()} {i.code}: {i.message}"))
    for c in r.review_concerns:
        print(_wrap(f"reviewer: {c}"))
    _h("6. FINAL INVESTIGATION REPORT")
    print(f"  incident: {r.incident_id}   dataset: {r.dataset_id}   status: {r.status.value}")
    print(_wrap(r.investigation_summary))
    print(f"  affected services: {', '.join(r.affected_services) or 'none'}")
    print(f"  affected components: {', '.join(r.affected_network_components) or 'none'}")
    ra = r.risk_and_impact_assessment
    print(
        f"  risk: max impact {ra.max_impact}; "
        + ", ".join(f"{s.service}(tier {s.tier})={s.impact}" for s in ra.services)
    )
    print("  recommended read-only diagnostic steps:")
    for s in r.recommended_diagnostic_steps:
        print(_wrap(f"{s.order}. {s.step}  [runbooks {s.runbook_ids}]", "    "))
    for s in r.removed_recommendations:
        print(_wrap(f"removed by policy: {s}", "    "))
    print(
        f"  runbooks: {[d.doc_id for d in r.relevant_runbooks]}  "
        f"historical: {[d.doc_id for d in r.historical_incident_references]}"
    )
    print(
        _wrap(f"escalation: {r.escalation.level} {r.escalation.targets} — {r.escalation.rationale}")
    )
    print(_wrap(f"confidence rationale: {r.confidence_rationale}"))
    for n in r.security_notes:
        print(_wrap(f"security: {n}"))
    m = r.run_metrics
    if m:
        print(
            f"  run: {m.llm_calls} model calls, {m.tool_calls} tool calls, {m.duration_s}s, "
            f"tokens={m.total_tokens if m.total_tokens is not None else 'n/a (mock)'}"
        )
    print(f"  session: {out.session_id} (resume with `opspilot resume {out.session_id}`)")
    if case and show_labels:
        _h("LABELS (shown after the run for comparison only; never given to agents)")
        print(json.dumps(case["labels"], indent=2))


async def _demo(args, settings: Settings) -> int:
    from opspilot.adk.runner import run_investigation
    from opspilot.adk.runtime import RunFaults
    from opspilot.datasets import spec

    _ensure_data(settings, [args.case])
    case = spec.get_case(args.case)
    _h(f"1. SYNTHETIC INCIDENT {case['id']}: {case['title']}")
    print(_wrap(f"engineer report: {case['request']}"))
    print(f"  dataset: {args.case}  model provider: {settings.provider}")
    faults = RunFaults.from_spec(case["run_faults"]) if settings.provider == "mock" else None
    out = await run_investigation(
        case["request"], args.case, settings=settings, faults=faults, persist=not args.no_persist
    )
    if args.json:
        print(out.report.model_dump_json(indent=2))
    else:
        print_outcome(out, case=case, show_labels=args.show_labels)
    return 0


async def _investigate(args, settings: Settings) -> int:
    from opspilot.adk.runner import run_investigation

    _ensure_data(settings, [args.dataset])
    out = await run_investigation(args.request, args.dataset, settings=settings)
    if args.json:
        print(out.report.model_dump_json(indent=2))
    else:
        print_outcome(out)
    return 0


async def _resume(args, settings: Settings) -> int:
    from opspilot.adk.runner import load_investigation, run_investigation

    found = await load_investigation(args.session_id, settings)
    if not found:
        print(f"no session {args.session_id}", file=sys.stderr)
        return 1
    st = found["state"]
    req = (st.get("scope") or {}).get("request_text") or "resume"
    out = await run_investigation(
        req, st["dataset_id"], settings=settings, session_id=args.session_id
    )
    print_outcome(out)
    return 0


async def _list(args, settings: Settings) -> int:
    from opspilot.adk.runner import list_investigations

    for i in await list_investigations(settings):
        print(f"{i['session_id']}  {i['dataset_id']}  {i['status']}  {i['updated']}")
    return 0


async def _show(args, settings: Settings) -> int:
    from opspilot.adk.runner import load_investigation

    found = await load_investigation(args.session_id, settings)
    if not found:
        print(f"no session {args.session_id}", file=sys.stderr)
        return 1
    print(json.dumps(found["state"].get("final_report"), indent=2))
    return 0


async def _eval(args, settings: Settings) -> int:
    from opspilot.eval.harness import run_eval, to_markdown, with_provider, write_results
    from opspilot.eval.perturbations import run_guardrail_eval

    s = with_provider(settings, args.live)
    results = await run_eval(s, args.cases or None)
    if args.guardrails and not args.live:
        results["guardrails"] = await run_guardrail_eval(s)
    jpath, mpath = write_results(results, Path(args.out))
    print(to_markdown(results))
    if "guardrails" in results:
        print(f"guardrail_detection_rate: {results['guardrails']['guardrail_detection_rate']}")
    print(f"results: {jpath}\n         {mpath}")
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(prog="opspilot", description=__doc__)
    p.add_argument("--live", action="store_true", help="use Gemini instead of the mock model")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-data", help="generate synthetic datasets")
    b.add_argument("cases", nargs="*")
    d = sub.add_parser("demo", help="run a labelled synthetic incident end to end")
    d.add_argument("--case", default="C03")
    d.add_argument("--json", action="store_true")
    d.add_argument("--show-labels", action="store_true")
    d.add_argument("--no-persist", action="store_true")
    i = sub.add_parser("investigate", help="investigate a free-text report against a dataset")
    i.add_argument("--dataset", required=True)
    i.add_argument("--request", required=True)
    i.add_argument("--json", action="store_true")
    r = sub.add_parser("resume", help="resume a persisted investigation")
    r.add_argument("session_id")
    sub.add_parser("list", help="list persisted investigations")
    s = sub.add_parser("show", help="print a persisted final report")
    s.add_argument("session_id")
    e = sub.add_parser("eval", help="run the evaluation suite")
    e.add_argument("--cases", nargs="*")
    e.add_argument("--guardrails", action="store_true")
    e.add_argument("--out", default=str(REPO_ROOT / "var" / "eval"))
    args = p.parse_args(argv)
    settings = Settings.from_env()
    if args.live or getattr(args, "live", False):
        settings = replace(settings, provider="gemini")
    if args.cmd == "build-data":
        from opspilot.datasets import generate

        paths = generate.build_all(settings.data_dir, args.cases or None)
        print("\n".join(str(x) for x in paths))
        return 0
    handler = {
        "demo": _demo,
        "investigate": _investigate,
        "resume": _resume,
        "list": _list,
        "show": _show,
        "eval": _eval,
    }[args.cmd]
    return asyncio.run(handler(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
