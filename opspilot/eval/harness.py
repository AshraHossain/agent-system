"""Run the labelled cases through the full ADK pipeline and score them.

Offline (default): ScriptedLlm — measures tools, orchestration, verification and
reporting, NOT model reasoning. Live: `--live` uses Gemini; the model, dataset
generator version, case list and git revision are recorded with the results.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from opspilot.adk.agents import TOOL_ALLOWLIST
from opspilot.adk.runner import run_investigation
from opspilot.adk.runtime import RunFaults
from opspilot.config import REPO_ROOT, Settings
from opspilot.datasets import generate, spec
from opspilot.eval.metrics import aggregate, score_case


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


async def run_eval(
    settings: Settings, case_ids: list[str] | None = None, *, build_data: bool = True
) -> dict:
    ids = case_ids or spec.case_ids()
    if build_data:
        generate.build_all(settings.data_dir, ids)
    scores, details = [], []
    for cid in ids:
        case = spec.get_case(cid)
        faults = (
            RunFaults.from_spec(case["run_faults"])
            if settings.provider == "mock"
            else RunFaults(
                telemetry_unavailable=RunFaults.from_spec(case["run_faults"]).telemetry_unavailable
            )
        )
        out = await run_investigation(
            case["request"], cid, settings=settings, faults=faults, persist=False
        )
        s = score_case(case, out.report, out.state, out.duration_s, TOOL_ALLOWLIST)
        scores.append(s)
        details.append(
            {"case_id": cid, "score": s.to_dict(), "report": out.report.model_dump(mode="json")}
        )
    return {
        "config": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "provider": settings.provider,
            "model": settings.model if settings.provider == "gemini" else "scripted-mock",
            "google_adk": version("google-adk"),
            "dataset_generator_version": generate.GENERATOR_VERSION,
            "cases": ids,
            "git_revision": _git_rev(),
            "python": platform.python_version(),
            "limits": settings.limits.__dict__,
            "note": (
                "mock provider measures the deterministic pipeline, not LLM reasoning"
                if settings.provider == "mock"
                else "live model run"
            ),
        },
        "aggregate": aggregate(scores),
        "cases": [s.to_dict() for s in scores],
        "details": details,
    }


def write_results(results: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = results["config"]["timestamp"].replace(":", "").replace("-", "")
    name = f"eval_{results['config']['provider']}_{stamp}"
    jpath = out_dir / f"{name}.json"
    jpath.write_text(json.dumps(results, indent=2, default=str))
    mpath = out_dir / f"{name}.md"
    mpath.write_text(to_markdown(results))
    return jpath, mpath


def to_markdown(results: dict) -> str:
    c, a = results["config"], results["aggregate"]
    lines = [
        f"# OpsPilot evaluation — {c['provider']} ({c['model']})",
        "",
        f"- ADK {c['google_adk']}, generator v{c['dataset_generator_version']}, "
        f"git {c['git_revision']}, {c['timestamp']}",
        f"- {c['note']}",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in a.items()]
    lines += [
        "",
        "| Case | Tags | Status (expected) | Esc | Top1 | Top3 | Cites valid | "
        "Runbook P/R | Tools | Latency s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in results["cases"]:
        lines.append(
            f"| {s['case_id']} | {', '.join(s['tags'])} | {s['status']} ({s['expected_status']}) "
            f"| {'✓' if s['escalation_correct'] else '✗'} | {s['top1']} | {s['top3']} | "
            f"{s['citations_valid']}/{s['citations_total']} | {s['runbook_precision']}/"
            f"{s['runbook_recall']} | {s['tool_recall']} | {s['latency_s']} |"
        )
    return "\n".join(lines) + "\n"


def with_provider(settings: Settings, live: bool) -> Settings:
    return replace(settings, provider="gemini") if live else settings
