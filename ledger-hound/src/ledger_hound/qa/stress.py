"""Synthetic stress test: many randomized POs, each with a known right answer.

The attack library proves *that* a control can break. This module measures
*how often* it breaks across realistic variety: 1-3 lines, float-trap
prices, full / partial / zero receipts, and invoices that are exact, under,
over by a unit, over by a cent, or price-inflated with lower quantity.

The method is differential testing. For every scenario, Python first works
out what a correct AP system must do (the ``Expectation``), using Decimal and
nothing from the API. Then the scenario is pushed through the API as far as
it will go (create -> match -> approve), and every gap between expected and
observed is recorded as a violation. Results are aggregated into a failure
rate overall, per rule, and per invoice variant.

Every scenario is generated from a seeded RNG, so ``--seed 7`` reproduces the
same 50 scenarios on any machine. That makes a failure report actionable.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .discovery import Catalog
from .http import ApiClient
from .models import slim_all
from .oracle import dec, gl_problems, gl_totals, match_is_clean, money, pick
from .workflow import Line, SetupError, Workflow, entity_id

# Prices chosen to include float traps: 0.57*100, 0.10*3, 4.98*10 and 1.15*3 are
# all off by a hair in binary floating point.
PRICE_POOL = ["0.10", "0.57", "0.07", "1.15", "4.98", "14.25", "19.99", "89.99"]

VARIANTS = {
    "exact": 35,  # invoice exactly what was received
    "under": 15,  # invoice less than received
    "over_qty": 15,  # one unit more than received
    "over_cent": 15,  # one cent above the PO unit price
    "price_up_qty_down": 20,  # half the qty at double the price: total fits, price doesn't
}

STRESS_ROLES = (
    "create_vendor",
    "create_po",
    "submit_po",
    "receive_po",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)


@dataclass
class Scenario:
    id: int
    variant: str
    po_lines: list[Line]
    received: list[tuple[str, Decimal]]
    invoice_lines: list[Line]


@dataclass
class Expectation:
    received_value: Decimal
    invoice_amount: Decimal
    refuse_at_create: bool  # invoice total above the value of goods received
    price_mismatch: bool
    qty_exceeds_receipt: bool
    fully_received: bool

    @property
    def reaches_gl(self) -> bool:
        return not (self.refuse_at_create or self.price_mismatch or self.qty_exceeds_receipt)

    @property
    def flag_partial(self) -> bool:
        return self.reaches_gl and not self.fully_received


@dataclass
class ScenarioResult:
    id: int
    variant: str
    expected: dict[str, Any]
    observed: dict[str, Any]
    violations: list[dict[str, str]] = field(default_factory=list)
    scenario: dict[str, Any] = field(default_factory=dict)
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    def violate(self, rule: str, detail: str) -> None:
        self.violations.append({"rule": rule, "detail": detail})


# ---------------------------------------------------------------- generation


def generate(n: int, seed: int, skus: list[str]) -> list[Scenario]:
    rng = random.Random(seed)
    names, weights = list(VARIANTS), list(VARIANTS.values())
    scenarios = []
    for i in range(1, n + 1):
        variant = rng.choices(names, weights)[0]
        picked = rng.sample(skus, k=min(len(skus), rng.randint(1, 3)))
        po_lines: list[Line] = []
        received: list[tuple[str, Decimal]] = []
        for j, sku in enumerate(picked):
            qty = rng.randint(2, 40)
            price = Decimal(rng.choice(PRICE_POOL + [f"{rng.randint(1, 20000) / 100:.2f}"]))
            po_lines.append((sku, Decimal(qty), price))
            mode = rng.choices(["full", "partial", "none"], [45, 40, 15])[0]
            if j == 0 and mode == "none":
                mode = "partial"  # the first line always has something to invoice
            got = {"full": qty, "partial": rng.randint(1, qty - 1), "none": 0}[mode]
            if variant == "price_up_qty_down" and j == 0:
                got = max(got, 2)  # needs at least 2 received to halve
            if got:
                received.append((sku, Decimal(got)))
        scenarios.append(
            Scenario(i, variant, po_lines, received, _invoice(rng, variant, po_lines, received))
        )
    return scenarios


def _invoice(
    rng: random.Random, variant: str, po_lines: list[Line], received: list[tuple[str, Decimal]]
) -> list[Line]:
    price_of = {s: p for s, _, p in po_lines}
    lines: list[Line] = [(s, r, price_of[s]) for s, r in received]
    sku, r, p = lines[0]
    if variant == "under" and r > 1:
        lines[0] = (sku, Decimal(rng.randint(1, int(r) - 1)), p)
    elif variant == "over_qty":
        lines[0] = (sku, r + Decimal(rng.randint(1, 3)), p)
    elif variant == "over_cent":
        lines[0] = (sku, r, p + Decimal("0.01"))
    elif variant == "price_up_qty_down":
        lines[0] = (sku, Decimal(int(r) // 2), p * 2)
    return lines


def expect(s: Scenario) -> Expectation:
    """What a correct AP system must do, computed without asking the API."""
    po = {sku: (qty, price) for sku, qty, price in s.po_lines}
    got = {sku: Decimal("0") for sku in po} | dict(s.received)
    received_value = sum((money(got[k] * po[k][1]) for k in po), Decimal("0"))
    invoice_amount = sum((money(q * p) for _, q, p in s.invoice_lines), Decimal("0"))
    return Expectation(
        received_value=received_value,
        invoice_amount=invoice_amount,
        refuse_at_create=invoice_amount > received_value,
        price_mismatch=any(p != po[k][1] for k, _, p in s.invoice_lines),
        qty_exceeds_receipt=any(q > got[k] for k, q, _ in s.invoice_lines),
        fully_received=all(got[k] >= po[k][0] for k in po),
    )


# ------------------------------------------------------------------ execution


async def run_scenario(wf: Workflow, s: Scenario) -> ScenarioResult:
    start = len(wf.client.log)
    res = await _run_scenario(wf, s)
    res.exchanges = slim_all(wf.client.log[start:])
    return res


async def _run_scenario(wf: Workflow, s: Scenario) -> ScenarioResult:
    e = expect(s)
    res = ScenarioResult(
        id=s.id,
        variant=s.variant,
        expected={
            "received_value": str(e.received_value),
            "invoice_amount": str(e.invoice_amount),
            "refuse_at_create": e.refuse_at_create,
            "reaches_gl": e.reaches_gl,
            "flag_partial": e.flag_partial,
        },
        observed={},
        scenario=_describe(s),
    )
    try:
        vendor = await wf.fresh_vendor()
        po = await wf.received_po(vendor, s.po_lines, receive=s.received)
    except SetupError as exc:
        res.violate("setup", str(exc))
        return res

    ex = await wf.create_invoice(vendor, po, wf.unique("INV"), s.invoice_lines)
    res.observed["create_status"] = ex.status
    if ex.status >= 500 or ex.status == 0:
        res.violate("server_error", f"create invoice: HTTP {ex.status}")
        return res
    if not ex.ok:
        if not e.refuse_at_create:
            res.violate(
                "false_rejection", f"valid invoice for {e.invoice_amount} refused: {ex.body}"
            )
        return res
    if e.refuse_at_create:
        res.violate(
            "overpayment_protection",
            f"invoice {e.invoice_amount} accepted against {e.received_value} received",
        )
    if (amount := dec(pick(ex.body, "amount", "total"))) != e.invoice_amount:
        res.violate("money_precision", f"invoice amount {amount} != expected {e.invoice_amount}")

    # Push it as far as it goes, like an attacker would.
    inv = entity_id(ex, "invoice_id")
    m = await wf.match(inv)
    res.observed["match"] = m.body if m.ok else f"HTTP {m.status}"
    if m.ok and e.flag_partial and match_is_clean(m.body):
        res.violate("partial_receipt_flag", "match passed cleanly on a partially received PO")
    a = await wf.approve(inv) if m.ok else None
    reached_gl = bool(a and a.ok)
    res.observed["reached_gl"] = reached_gl

    if reached_gl and not e.reaches_gl:
        rule = (
            "price_variance"
            if e.price_mismatch and not e.refuse_at_create
            else ("overpayment_protection")
        )
        if not any(v["rule"] == rule for v in res.violations):
            res.violate(rule, "invoice that should have been stopped was posted to the GL")
    if e.reaches_gl and not reached_gl:
        res.violate("false_rejection", "valid invoice never reached the GL")
    if reached_gl:
        await _check_books(wf, res, a.body, inv, vendor, e)
    return res


async def _check_books(
    wf: Workflow, res: ScenarioResult, approve_body: Any, inv: Any, vendor: Any, e: Expectation
) -> None:
    entry = pick(approve_body, "gl_entry", "journal_entry", "gl")
    if entry is None and wf.catalog.has("gl_entries"):
        gl = await wf.gl_for(inv)
        entry = gl.body[0] if gl.ok and isinstance(gl.body, list) and gl.body else None
    if entry is None:
        res.violate("gl_balance", "approved but no GL entry found")
        return
    for problem in gl_problems(entry):
        res.violate("gl_balance", problem)
    posted = dec(gl_totals(entry)["credits"])
    res.observed["gl_credit"] = str(posted)
    if wf.catalog.has("vendor_exposure"):
        open_ap = dec(await wf.open_ap(vendor))
        res.observed["open_ap"] = str(open_ap)
        if open_ap != posted:
            res.violate("exposure_integrity", f"open AP {open_ap} != posted {posted}")


def _describe(s: Scenario) -> dict[str, Any]:
    def fmt(lines: list[Line]) -> list[list[str]]:
        return [[sku, str(q), str(p)] for sku, q, p in lines]

    return {
        "po_lines": fmt(s.po_lines),
        "received": [[sku, str(q)] for sku, q in s.received],
        "invoice_lines": fmt(s.invoice_lines),
    }


async def stress(
    client: ApiClient, catalog: Catalog, n: int = 50, seed: int = 7, concurrency: int = 1
) -> dict[str, Any]:
    """Run ``n`` scenarios, at most ``concurrency`` at a time.

    Each scenario gets its own vendor, PO and log, so running them side by side
    changes the load on the API, not the expected outcome: any difference from a
    sequential run is itself a finding (shared state or a missing lock).
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    missing = catalog.missing(*STRESS_ROLES, "list_items")
    if missing:
        return {"error": f"API lacks roles needed for stress: {missing}"}
    items = await Workflow(client, catalog).list_items()
    skus = [s for s in (pick(i, "sku", "code", "id") for i in items.body) if s]
    gate = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def one(s: Scenario) -> ScenarioResult:
        async with gate:
            own = client.view()
            result = await run_scenario(Workflow(own, catalog, f"stress{seed}s{s.id}"), s)
            latencies.extend(x.elapsed_ms for x in own.log)
            return result

    start = time.perf_counter()
    results = list(await asyncio.gather(*(one(s) for s in generate(n, seed, skus))))
    elapsed = time.perf_counter() - start
    out = aggregate(results, n, seed)
    latencies.sort()
    out["load"] = {
        "concurrency": concurrency,
        "elapsed_s": round(elapsed, 3),
        "scenarios_per_s": round(n / elapsed, 2) if elapsed else None,
        "http_calls": len(latencies),
        "latency_ms_p50": _pct(latencies, 0.50),
        "latency_ms_p95": _pct(latencies, 0.95),
    }
    return out


def _pct(values: list[float], q: float) -> float | None:
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def aggregate(results: list[ScenarioResult], n: int, seed: int) -> dict[str, Any]:
    failed = [r for r in results if r.violations]
    by_rule: dict[str, int] = {}
    for r in failed:
        for rule in {v["rule"] for v in r.violations}:
            by_rule[rule] = by_rule.get(rule, 0) + 1
    by_variant: dict[str, dict[str, int]] = {}
    for r in results:
        row = by_variant.setdefault(r.variant, {"scenarios": 0, "failed": 0})
        row["scenarios"] += 1
        row["failed"] += bool(r.violations)
    return {
        "seed": seed,
        "scenarios": n,
        "failed": len(failed),
        "failure_rate": round(len(failed) / n, 4) if n else 0.0,
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "by_variant": by_variant,
        "results": [asdict(r) for r in results],
    }
