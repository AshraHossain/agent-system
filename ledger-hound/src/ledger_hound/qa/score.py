"""Measure the agent against the practice API's planted bugs.

For each bug: start a fresh API with only that bug switched on, run the attack
library, and count the bug as detected if its ``caught_by`` attack reports
BREACHED. A clean API (no bugs) must report zero breaches, which is the
false-positive check. This is how the agent itself gets tested.
"""

from __future__ import annotations

from typing import Any

from ..p2p_api.app import create_app
from ..p2p_api.bugs import BUGS
from .adversary import Adversary
from .discovery import discover
from .explorer import Explorer
from .http import ApiClient


async def _run(bugs: frozenset[str]) -> tuple[str, dict[str, str]]:
    client = ApiClient.for_app(create_app(bugs))
    try:
        catalog = await discover(client)
        happy = await Explorer(client, catalog).run()
        results = await Adversary(client, catalog).run()
    finally:
        await client.aclose()
    return happy["status"], {r.attack: r.status for r in results}


async def score() -> dict[str, Any]:
    clean_happy, clean = await _run(frozenset())
    false_positives = sorted(a for a, s in clean.items() if s != "HELD")

    rows = []
    for bug in BUGS.values():
        happy, statuses = await _run(frozenset({bug.name}))
        rows.append(
            {
                "bug": bug.name,
                "rule": bug.rule,
                "caught_by": bug.caught_by,
                "detected": statuses.get(bug.caught_by) == "BREACHED",
                "happy_path": happy,
                "also_breached": sorted(
                    a for a, s in statuses.items() if s == "BREACHED" and a != bug.caught_by
                ),
            }
        )
    detected = sum(r["detected"] for r in rows)
    return {
        "detection_rate": f"{detected}/{len(rows)}",
        "false_positives_on_clean_api": false_positives,
        "clean_happy_path": clean_happy,
        "bugs": rows,
    }


def grade(key: dict[str, Any], report: Any) -> dict[str, Any]:
    """Grade a report against a blind-practice answer key, by rule.

    Accepts a Ledger Hound report or any report in the brief's shape: a list of
    ``{"rule", "status"}`` entries, bare or under ``"adversarial"``. A hidden bug
    counts as caught when its rule is reported BREACHED. Other breached rules are
    listed, not penalised: one bug often breaks a second rule downstream.
    """
    rows = report.get("adversarial", []) if isinstance(report, dict) else report
    breached = {r.get("rule") for r in rows if str(r.get("status", "")).upper() == "BREACHED"}
    hidden = key["bugs"]
    caught = [b for b in hidden if b["rule"] in breached]
    return {
        "score": f"{len(caught)}/{len(hidden)}",
        "caught": caught,
        "missed": [b for b in hidden if b["rule"] not in breached],
        "also_breached": sorted(breached - {b["rule"] for b in hidden}),
    }
