"""Run the labelled cases through the full ADK pipeline and score them.

Offline (default): ScriptedLlm — measures tools, orchestration, verification and
reporting, NOT model reasoning. Live: `--live` uses Gemini; the model, dataset
generator version, case list and git revision are recorded with the results.

`repeat` runs every case several times (live runs are nondeterministic) and adds
the spread across repetitions. Every run records, per agent, rejected, text-only
and replaced final answers. With `baseline`, a live evaluation also runs the mock
on the same cases so the report can compare the two.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from opspilot.adk.agents import TOOL_ALLOWLIST
from opspilot.adk.runner import run_investigation
from opspilot.adk.runtime import RunFaults
from opspilot.config import REPO_ROOT, Settings
from opspilot.datasets import generate, spec
from opspilot.eval.metrics import (
    FALLBACK_REASONS,
    CaseScore,
    agent_compliance,
    aggregate,
    case_stability,
    fallback_count,
    score_case,
    spread,
)

MAX_REPEAT = 20
# Metrics compared between the mock baseline and a live run.
COMPARED = (
    "completion_rate",
    "status_accuracy",
    "escalation_accuracy",
    "root_cause_top1",
    "root_cause_top3",
    "false_alarm_rate",
    "summary_correctness",
    "citation_validity",
    "citation_relevance",
    "unsupported_claim_rate",
    "runbook_precision",
    "runbook_recall",
    "missing_evidence_detection",
    "malicious_document_handling",
    "tool_invocation_recall",
    "llm_calls_total",
    "latency_s_mean",
)


def _git_rev() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - fixed argv, no shell
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
        ).stdout.strip()
    except Exception:
        return None


def _faults(case: dict, provider: str) -> RunFaults:
    faults = RunFaults.from_spec(case["run_faults"])
    if provider == "mock":
        return faults
    # Model faults can only be injected into the mock; the data fault applies to both.
    return RunFaults(telemetry_unavailable=faults.telemetry_unavailable)


def _progress_line(n: int, total: int, s: CaseScore, repeat: int) -> str:
    top1 = "-" if s.top1 is None else ("hit" if s.top1 else "miss")
    run = f" run {s.repetition}/{repeat}" if repeat > 1 else ""
    extra = f", {k} fallback(s)" if (k := fallback_count(s)) else ""
    extra += ", ABORTED" if s.aborted else ""
    return f"[{n}/{total}] {s.case_id}{run}: {s.status}, top-1 {top1}, {s.latency_s:.1f}s{extra}"


async def _run_cases(
    settings: Settings,
    ids: list[str],
    repeat: int,
    progress: Callable[[str], None] | None,
) -> tuple[list[CaseScore], list[dict]]:
    """Every case once per repetition; a whole pass over the cases before the next."""
    scores, details = [], []
    total = len(ids) * repeat
    for rep in range(1, repeat + 1):
        for cid in ids:
            case = spec.get_case(cid)
            out = await run_investigation(
                case["request"],
                cid,
                settings=settings,
                faults=_faults(case, settings.provider),
                persist=False,
            )
            s = score_case(
                case,
                out.report,
                out.state,
                out.duration_s,
                TOOL_ALLOWLIST,
                repetition=rep,
                aborted=out.aborted,
            )
            scores.append(s)
            details.append(
                {
                    "case_id": cid,
                    "repetition": rep,
                    "score": s.to_dict(),
                    "report": out.report.model_dump(mode="json"),
                }
            )
            if progress:
                progress(_progress_line(len(scores), total, s, repeat))
    return scores, details


def _per_repetition(scores: list[CaseScore], repeat: int) -> list[dict]:
    return [aggregate([s for s in scores if s.repetition == r]) for r in range(1, repeat + 1)]


async def run_eval(
    settings: Settings,
    case_ids: list[str] | None = None,
    *,
    build_data: bool = True,
    split: str = "main",
    repeat: int = 1,
    baseline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict:
    if not 1 <= repeat <= MAX_REPEAT:
        raise ValueError(f"repeat must be between 1 and {MAX_REPEAT}, got {repeat}")
    ids = case_ids or spec.case_ids(split)
    skipped = []
    if settings.provider != "mock":
        # Model timeouts/quota errors can only be injected into the mock model.
        skipped = [
            c for c in ids if {"model_timeout", "model_quota"} & set(spec.get_case(c)["run_faults"])
        ]
        ids = [c for c in ids if c not in skipped]
    if build_data:
        generate.build_all(settings.data_dir, ids)
    scores, details = await _run_cases(settings, ids, repeat, progress)
    live = settings.provider != "mock"
    results = {
        "config": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": settings.provider,
            "model": settings.model if settings.provider == "gemini" else "scripted-mock",
            "embedder": settings.embedding.label,
            "google_adk": version("google-adk"),
            "dataset_generator_version": generate.GENERATOR_VERSION,
            "split": split,
            "cases": ids,
            "skipped_cases": skipped,
            "repetitions": repeat,
            "git_revision": _git_rev(),
            "python": platform.python_version(),
            "limits": settings.limits.__dict__,
            "note": "live model run"
            if live
            else "mock provider measures the deterministic pipeline, not LLM reasoning",
        },
        "aggregate": aggregate(scores),
        "agents": agent_compliance(scores),
        "cases": [s.to_dict() for s in scores],
        "details": details,
    }
    if repeat > 1:
        results["repetitions"] = {
            "spread": spread(_per_repetition(scores, repeat)),
            "per_case": case_stability(scores),
        }
    if baseline and live:
        mock_scores, _ = await _run_cases(replace(settings, provider="mock"), ids, 1, None)
        results["baseline"] = {
            "provider": "mock",
            "model": "scripted-mock",
            "aggregate": aggregate(mock_scores),
            "agents": agent_compliance(mock_scores),
            "cases": [s.to_dict() for s in mock_scores],
            "comparison": _comparison(mock_scores, scores, repeat),
        }
    return results


def _comparison(mock: list[CaseScore], live: list[CaseScore], repeat: int) -> dict:
    """Mock value vs the live spread across repetitions, per metric and per case."""
    mock_agg = aggregate(mock)
    live_spread = spread(_per_repetition(live, repeat))
    stability = case_stability(live)
    return {
        "metrics": {
            m: {"mock": mock_agg.get(m), "live": live_spread.get(m)}
            for m in COMPARED
            if mock_agg.get(m) is not None or m in live_spread
        },
        "cases": {
            s.case_id: {
                "mock_status": s.status,
                "mock_top1": s.top1,
                "live": stability[s.case_id],
            }
            for s in mock
        },
    }


def write_results(results: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    c = results["config"]
    stamp = c["timestamp"].replace(":", "").replace("-", "")
    reps = f"_x{c['repetitions']}" if c.get("repetitions", 1) > 1 else ""
    name = f"eval_{c['provider']}_{c.get('split', 'main')}{reps}_{stamp}"
    jpath = out_dir / f"{name}.json"
    jpath.write_text(json.dumps(results, indent=2, default=str))
    mpath = out_dir / f"{name}.md"
    mpath.write_text(to_markdown(results))
    return jpath, mpath


def _num(v) -> str:
    return "–" if v is None else f"{v:g}" if isinstance(v, float) else str(v)


def _k_of(k: int | None, n: int) -> str:
    return "–" if k is None else f"{k}/{n}"


def _agents_table(agents: dict) -> list[str]:
    reasons = " / ".join(r.replace("_", " ") for r in FALLBACK_REASONS)
    lines = [
        "",
        "## Output compliance by agent",
        "",
        "Schema retries: `set_model_response` calls rejected by validation (the model "
        "tried again). Text answers: final answers sent as text by an agent that should "
        "call `set_model_response`. Fallbacks: outputs replaced by the agent's failed "
        "output. Clean: none of these.",
        "",
        f"| Agent | Runs | Model calls | Schema retries | Text answers | Fallbacks ({reasons}) "
        "| Clean runs |",
        "|---|---|---|---|---|---|---|",
    ]
    for agent, a in agents.items():
        fb = " / ".join(str(a["fallbacks"][r]) for r in FALLBACK_REASONS)
        lines.append(
            f"| {agent} | {a['runs']} | {a['llm_calls']} | {a['schema_retries']} | "
            f"{a['text_answers']} | {fb} | {a['clean_runs']}/{a['runs']} |"
        )
    return lines


def _stability_table(per_case: dict) -> list[str]:
    lines = [
        "",
        "| Case | Statuses (expected) | Status ok | Esc ok | Top1 | Top3 | Fallbacks | Aborted "
        "| Latency s (min–max) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, c in per_case.items():
        n = c["runs"]
        statuses = ", ".join(f"{k} ×{v}" for k, v in c["statuses"].items())
        lat = c["latency_s"]
        lines.append(
            f"| {cid} | {statuses} ({c['expected_status']}) | {c['status_correct']}/{n} | "
            f"{c['escalation_correct']}/{n} | {_k_of(c['top1_hits'], n)} | "
            f"{_k_of(c['top3_hits'], n)} | {c['fallbacks']} | {c['aborted']} | "
            f"{lat['min']:.1f}–{lat['max']:.1f} |"
        )
    return lines


def _comparison_tables(comparison: dict, repeat: int) -> list[str]:
    lines = [
        "",
        "## Live vs mock (same cases)",
        "",
        "The mock is deterministic (one run); live values are the mean and range over "
        f"{repeat} repetition(s). Mock scores measure the pipeline, not reasoning.",
        "",
        "| Metric | Mock | Live mean | Live min–max |",
        "|---|---|---|---|",
    ]
    for metric, v in comparison["metrics"].items():
        live = v["live"]
        span = f"{_num(live['min'])}–{_num(live['max'])}" if live else "–"
        lines.append(
            f"| {metric} | {_num(v['mock'])} | {_num(live['mean'] if live else None)} | {span} |"
        )
    lines += [
        "",
        "| Case | Mock status | Live statuses | Mock top1 | Live top1 | Live fallbacks |",
        "|---|---|---|---|---|---|",
    ]
    for cid, c in comparison["cases"].items():
        live = c["live"]
        statuses = ", ".join(f"{k} ×{v}" for k, v in live["statuses"].items())
        mock_top1 = "–" if c["mock_top1"] is None else ("✓" if c["mock_top1"] else "✗")
        lines.append(
            f"| {cid} | {c['mock_status']} | {statuses} | {mock_top1} | "
            f"{_k_of(live['top1_hits'], live['runs'])} | {live['fallbacks']} |"
        )
    return lines


def to_markdown(results: dict) -> str:
    c, a = results["config"], results["aggregate"]
    repeat = c.get("repetitions", 1)
    runs = f", {repeat} repetitions" if repeat > 1 else ""
    lines = [
        f"# OpsPilot evaluation — {c.get('split', 'main')} split, {c['provider']} "
        f"({c['model']}){runs}",
        "",
        f"- ADK {c['google_adk']}, generator v{c['dataset_generator_version']}, "
        f"git {c['git_revision']}, {c['timestamp']}",
        f"- {c['note']}",
    ]
    if skipped := c.get("skipped_cases"):
        lines.append(f"- skipped (faults only the mock can inject): {', '.join(skipped)}")
    lines.append("")
    if repeat > 1:
        lines += [
            "Spread across repetitions (each repetition is one pass over the cases):",
            "",
            "| Metric | Mean | Min | Max | Stdev |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {_num(v['mean'])} | {_num(v['min'])} | {_num(v['max'])} | "
            f"{_num(v['stdev'])} |"
            for k, v in results["repetitions"]["spread"].items()
        ]
    else:
        lines += ["| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in a.items() if not isinstance(v, dict)]
    if a.get("cost_by_agent"):
        lines += ["", f"| Agent | Cost ({a.get('cost_currency')}) |", "|---|---|"]
        lines += [f"| {agent} | {cost} |" for agent, cost in a["cost_by_agent"].items()]
    if results.get("agents"):
        lines += _agents_table(results["agents"])
    if repeat > 1:
        lines += _stability_table(results["repetitions"]["per_case"])
    else:
        lines += [
            "",
            "| Case | Tags | Status (expected) | Esc | Top1 | Top3 | Cites valid | "
            "Runbook P/R | Tools | Latency s |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in results["cases"]:
            lines.append(
                f"| {s['case_id']} | {', '.join(s['tags'])} | {s['status']} "
                f"({s['expected_status']}) | {'✓' if s['escalation_correct'] else '✗'} | "
                f"{s['top1']} | {s['top3']} | {s['citations_valid']}/{s['citations_total']} | "
                f"{s['runbook_precision']}/{s['runbook_recall']} | {s['tool_recall']} | "
                f"{s['latency_s']} |"
            )
    if results.get("baseline"):
        lines += _comparison_tables(results["baseline"]["comparison"], repeat)
    return "\n".join(lines) + "\n"


def with_provider(settings: Settings, live: bool) -> Settings:
    return replace(settings, provider="gemini") if live else settings
