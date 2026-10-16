"""Judge: run the whole investigation and produce the structured report.

Order of work:

1. discover   - map the API (OpenAPI, falling back to the brief's endpoint table)
2. plan       - the brain ranks the attack library by risk for this API
3. explore    - happy path (LLM agent in live mode if asked, scripted otherwise)
4. attack     - library probes in plan order; optional LLM-improvised probes
5. explain    - the brain writes the business impact of each breach
6. summarize  - per-rule rollup + executive summary

Verdicts come from the explorer/adversary oracles. The brain only adds words.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import __version__
from .adversary import ATTACKS, Adversary
from .brain import Brain, LiveBrain
from .discovery import discover
from .explorer import Explorer
from .http import ApiClient
from .models import AttackResult

STATUS_RANK = {"BREACHED": 0, "ERROR": 1, "HELD": 2, "SKIPPED": 3}


async def investigate(
    client: ApiClient,
    brain: Brain,
    *,
    target: str,
    agent_explore: bool = False,
    improvise: int = 0,
    only: list[str] | None = None,
) -> dict[str, Any]:
    notes: list[str] = []
    catalog = await discover(client)

    attack_meta = [
        {"id": a.id, "rule": a.rule, "title": a.title, "severity": a.severity}
        for a in ATTACKS
        if not only or a.id in only
    ]
    plan = await brain.plan(attack_meta, catalog.describe())

    happy: dict[str, Any] | None = None
    if agent_explore and isinstance(brain, LiveBrain):
        from .agentic import explore_with_agent

        try:
            happy = await explore_with_agent(client, catalog, brain)
        except Exception as exc:  # noqa: BLE001 - fall back to the scripted path
            notes.append(
                f"agent explorer failed, used scripted path: {type(exc).__name__}: {exc}"[:300]
            )
    elif agent_explore:
        notes.append("--agent-explore needs a live model; used scripted path")
    if happy is None:
        happy = await Explorer(client, catalog).run()
        happy["mode"] = "scripted"
    for step, text in zip(happy["steps"], await brain.annotate(happy["steps"]), strict=False):
        step["interpretation"] = text

    adversary = Adversary(client, catalog)
    results = await adversary.run(only=plan["order"])
    by_id = {r.attack: r for r in results}
    results = [by_id[i] for i in plan["order"] if i in by_id]

    if improvise and isinstance(brain, LiveBrain):
        from .agentic import improvise_attacks

        try:
            results += await improvise_attacks(client, catalog, brain, results, n=improvise)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"improviser failed: {type(exc).__name__}: {exc}"[:300])
    elif improvise:
        notes.append("--improvise needs a live model; skipped")

    breaches = [r for r in results if r.status == "BREACHED"]
    for r, text in zip(
        breaches, await brain.explain([r.to_dict() for r in breaches]), strict=False
    ):
        r.explanation = text

    rules = rollup(results)
    counts = {s: sum(1 for r in results if r.status == s) for s in STATUS_RANK}
    facts = {
        "happy_path": happy["status"],
        "total": len(results),
        "counts": counts,
        "breached_rules": [k for k, v in rules.items() if v == "BREACHED"],
        "breaches": [
            {"rule": r.rule, "title": r.title, "severity": r.severity, "observed": r.observed}
            for r in breaches
        ],
    }
    summary = await brain.summarize(facts)

    return {
        "meta": {
            "tool": "ledger-hound",
            "version": __version__,
            "target": target,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "brain": brain.name,
            "discovery": catalog.source,
            "roles": sorted(catalog.roles),
            "notes": notes + list(brain.notes),
            "http_calls": len(client.log),
        },
        "plan": plan,
        "happy_path": happy,
        "adversarial": [_entry(r) for r in results],
        "rules": rules,
        "counts": counts,
        "summary": summary,
    }


def rollup(results: list[AttackResult]) -> dict[str, str]:
    """Worst status per rule (BREACHED > ERROR > HELD > SKIPPED), library probes only."""
    out: dict[str, str] = {}
    for r in results:
        if r.source != "library":
            continue
        current = out.get(r.rule)
        if current is None or STATUS_RANK[r.status] < STATUS_RANK[current]:
            out[r.rule] = r.status
    return out


def _entry(r: AttackResult) -> dict[str, Any]:
    d = r.to_dict()
    # Challenge format first: rule / status / evidence, then the detail.
    return {
        "rule": d.pop("rule"),
        "status": d.pop("status"),
        "evidence": {**d.pop("evidence"), "exchanges": d.pop("exchanges")},
        **d,
    }
