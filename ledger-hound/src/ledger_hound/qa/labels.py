"""Labelled dataset for the violation classifier, built from the planted bugs.

DSPy needs labelled examples to optimize a prompt against. Collecting them by
hand would be slow and subjective. Here they cost nothing and are correct by
construction:

* run the attack library and the stress test against the clean practice API
  and against each planted bug;
* every probe becomes one example: the rule, what was attempted, and the raw
  HTTP exchanges;
* the label is the **oracle's** verdict (BREACHED / a stress violation means
  ``violation=True``), which the detection tests have already validated.

Leakage guard: the model sees only the rule text, the attempt (what the
probe tried and what *should* happen) and the API's own requests and
responses. It never sees the oracle's ``observed`` text, the status, or the
evidence numbers. ``tests/test_dspy_opt.py`` checks this.

No LLM is needed to build the dataset; it is pure Python.
"""

from __future__ import annotations

import json
import random
from typing import Any

from ..p2p_api.app import create_app
from ..p2p_api.bugs import BUGS
from .adversary import ATTACKS, Adversary
from .discovery import discover
from .http import ApiClient
from .models import AttackResult
from .stress import stress

RULE_TEXT: dict[str, str] = {
    "overpayment_protection": "The total invoiced against a PO must never exceed the value of goods actually received.",
    "match_gate": "An invoice cannot be approved unless it has passed 3-way match (PO + receipt + invoice).",
    "partial_receipt_flag": "If goods are only partially received, the match must surface this, not silently pass.",
    "inactive_vendor_gate": "No new purchase orders against inactive vendors.",
    "gl_balance": "Every approved invoice must produce a balanced GL entry (debits = credits) at cent precision.",
    "duplicate_invoice_detection": "The same invoice_number from the same vendor must be rejected, ignoring case and surrounding spaces.",
    "receipt_quantity": "Total received quantity on a PO line must never exceed the ordered quantity.",
    "exposure_integrity": "Vendor open AP must equal the sum of invoices posted exactly once; repeat approvals must not post again.",
    "concurrency": "Simultaneous approvals of one invoice must result in exactly one GL posting.",
    "vendor_consistency": "A vendor may only invoice against its own purchase orders.",
    "input_validation": "Quantities and prices must be greater than zero.",
    "price_variance": "An invoice priced above the PO unit price must not pass matching or reach the GL.",
    "state_machine": "Goods cannot be received on a PO that has not been submitted.",
    "any": (
        "All P2P financial controls: invoice total <= received value; 3-way match before approval; "
        "partial receipts flagged at match; invoice prices equal PO prices; balanced GL at cent "
        "precision; vendor exposure equals amounts posted; valid invoices must not be refused."
    ),
}

MAX_EXCHANGES = 14
MAX_BODY = 300


def render_exchanges(exchanges: list[dict[str, Any]]) -> str:
    """Compact transcript: one line per call, bodies truncated, newest calls kept."""
    lines = []
    for ex in exchanges[-MAX_EXCHANGES:]:
        req, resp = ex["request"], ex["response"]
        body = "" if req["body"] is None else " " + json.dumps(req["body"], default=str)[:MAX_BODY]
        out = json.dumps(resp["body"], default=str)[:MAX_BODY]
        lines.append(f"{req['method']} {req['path']}{body}\n  -> {resp['status']} {out}")
    return "\n".join(lines)


def from_attack(r: AttackResult, bugs: str) -> dict[str, Any] | None:
    if r.status not in {"HELD", "BREACHED"} or r.source != "library":
        return None
    attack = next(a for a in ATTACKS if a.id == r.attack)
    return {
        "id": f"attack:{r.attack}:{bugs or 'clean'}",
        "group": f"attack:{r.attack}:{r.status}",
        "rule": RULE_TEXT[r.rule],
        "attempt": f"{attack.title}. A correct system: {attack.expected}.",
        "exchanges": render_exchanges(r.exchanges),
        "violation": r.status == "BREACHED",
    }


def from_scenario(res: dict[str, Any], bugs: str) -> dict[str, Any]:
    sc = res["scenario"]
    rules = sorted({v["rule"] for v in res["violations"]})
    po = ", ".join(f"{q} x {s} @ {p}" for s, q, p in sc["po_lines"])
    got = ", ".join(f"{q} x {s}" for s, q in sc["received"]) or "nothing"
    inv = ", ".join(f"{q} x {s} @ {p}" for s, q, p in sc["invoice_lines"])
    return {
        "id": f"stress:{res['id']}:{bugs or 'clean'}",
        "group": f"stress:{res['variant']}:{'+'.join(rules) or 'ok'}",
        "rule": RULE_TEXT["any"],
        "attempt": (
            f"PO for {po}; received {got}; vendor invoices {inv} "
            f"({res['variant']}), then match and approve are attempted."
        ),
        "exchanges": render_exchanges(res["exchanges"]),
        "violation": bool(res["violations"]),
    }


STRESS_BUGS = ("overpay_ordered", "float_money", "silent_partial", "no_price_check")


async def build_dataset(
    stress_n: int = 24, seed: int = 7, per_group: int = 2
) -> list[dict[str, Any]]:
    """Run probes on the clean API and every planted bug; dedupe; return labelled rows."""
    rows: list[dict[str, Any]] = []
    for bugs in [frozenset(), *(frozenset({b}) for b in BUGS)]:
        name = ",".join(sorted(bugs))
        client = ApiClient.for_app(create_app(bugs))
        try:
            catalog = await discover(client)
            for r in await Adversary(client, catalog).run():
                if (row := from_attack(r, name)) is not None:
                    rows.append(row)
            if not bugs or name in STRESS_BUGS:
                result = await stress(client, catalog, n=stress_n, seed=seed)
                rows += [from_scenario(res, name) for res in result["results"]]
        finally:
            await client.aclose()
    return dedupe(rows, per_group)


def dedupe(rows: list[dict[str, Any]], per_group: int) -> list[dict[str, Any]]:
    """Keep at most ``per_group`` rows per (probe, outcome) so no case dominates training."""
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        if seen.get(row["group"], 0) < per_group:
            seen[row["group"]] = seen.get(row["group"], 0) + 1
            out.append(row)
    return out


def split(
    rows: list[dict[str, Any]], dev_fraction: float = 0.4, seed: int = 7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stratified by label, seeded: same split every time, both labels on both sides."""
    rng = random.Random(seed)
    train, dev = [], []
    for label in (True, False):
        group = [r for r in rows if r["violation"] is label]
        rng.shuffle(group)
        cut = round(len(group) * dev_fraction)
        dev += group[:cut]
        train += group[cut:]
    return train, dev
