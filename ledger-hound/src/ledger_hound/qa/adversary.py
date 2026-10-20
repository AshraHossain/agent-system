"""Adversarial agent, attack library.

Each attack is a small, deterministic probe: build the minimum valid state
with a fresh vendor (so attacks never interfere with one another), make one
move that should be refused, then check what actually happened.

Verdicts:

* HELD     - the API refused the move, or the bad state never reached the books
* BREACHED - the move went through; ``evidence`` holds the numbers that prove it
* ERROR    - setup failed or the server crashed (5xx): a human must look
* SKIPPED  - the API lacks an operation the probe needs (e.g. no way to deactivate)

The judgement never rests on a status code alone. Where it matters, the probe
reads the PO, the GL or the exposure afterwards and checks the arithmetic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .discovery import Catalog
from .http import ApiClient, Exchange
from .models import AttackResult, AttackStatus, slim_all
from .oracle import (
    dec,
    expected_amount,
    gl_problems,
    gl_totals,
    match_is_clean,
    pick,
    surfaces_partial,
)
from .workflow import Line, SetupError, Workflow, entity_id

TEN = Decimal("10")
PRICE = Decimal("10.00")

Outcome = tuple[AttackStatus, str, dict[str, Any]]


def held(observed: str, **evidence: Any) -> Outcome:
    return "HELD", observed, evidence


def breached(observed: str, **evidence: Any) -> Outcome:
    return "BREACHED", observed, evidence


def crashed(ex: Exchange) -> Outcome:
    return (
        "ERROR",
        f"server answered HTTP {ex.status}; guardrail outcome unknown",
        {"body": ex.body},
    )


class Probe:
    """Per-attack context handed to each attack function."""

    def __init__(self, wf: Workflow, skus: list[str]):
        self.wf = wf
        self.skus = skus

    def sku(self, i: int = 0) -> str:
        return self.skus[i % len(self.skus)]

    def line(self, qty: Decimal = TEN, price: Decimal = PRICE, i: int = 0) -> list[Line]:
        return [(self.sku(i), qty, price)]

    async def push_through(self, invoice_id: Any) -> dict[str, Any]:
        """Try to take an invoice all the way to the books; report where it stopped."""
        m = await self.wf.match(invoice_id)
        if not m.ok:
            return {"reached_gl": False, "blocked_at": "match", "match": m.body}
        a = await self.wf.approve(invoice_id)
        if not a.ok:
            return {
                "reached_gl": False,
                "blocked_at": "approve",
                "match": m.body,
                "approve": a.body,
            }
        return {"reached_gl": True, "blocked_at": None, "match": m.body}

    async def gl_count(self, invoice_id: Any) -> int | None:
        if not self.wf.catalog.has("gl_entries"):
            return None
        ex = await self.wf.gl_for(invoice_id)
        return len(ex.body) if ex.ok and isinstance(ex.body, list) else None


@dataclass(frozen=True)
class Attack:
    id: str
    rule: str
    title: str
    expected: str
    severity: str
    needs: tuple[str, ...]
    run: Callable[[Probe], Awaitable[Outcome]]


ATTACKS: list[Attack] = []


def attack(id: str, rule: str, title: str, expected: str, severity: str, *needs: str):
    base = ("create_vendor", "create_po", "submit_po", "receive_po")

    def register(fn: Callable[[Probe], Awaitable[Outcome]]):
        ATTACKS.append(Attack(id, rule, title, expected, severity, base + needs, fn))
        return fn

    return register


# ------------------------------------------------------------- the six rules


@attack(
    "approve_unmatched",
    "match_gate",
    "Approve an invoice that was never matched",
    "approve refused until 3-way match passes",
    "critical",
    "create_invoice",
    "approve_invoice",
)
async def approve_unmatched(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    inv = await p.wf.invoice(v, po, p.line())
    ex = await p.wf.approve(inv)
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        return breached("unmatched invoice was approved and posted", approve=ex.body)
    return held(f"approve refused with HTTP {ex.status}")


@attack(
    "invoice_exceeds_received",
    "overpayment_protection",
    "Invoice the full PO when only 4 of 10 units arrived",
    "invoice above received value (40.00) refused",
    "critical",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def invoice_exceeds_received(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(), receive=[(p.sku(), Decimal("4"))])
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line())
    evidence = {"received_value": "40.00", "invoice_amount": "100.00"}
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"invoice refused with HTTP {ex.status}", **evidence)
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    where = (
        "and was approved to the GL"
        if downstream["reached_gl"]
        else (f"(stopped later at {downstream['blocked_at']})")
    )
    return breached(
        f"invoice for 100.00 accepted against 40.00 received {where}",
        **evidence,
        downstream=downstream,
    )


@attack(
    "invoice_cumulative_overbill",
    "overpayment_protection",
    "Two invoices of 60.00 against 100.00 received",
    "second invoice refused: 60 + 60 > 100",
    "critical",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def invoice_cumulative_overbill(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    six = p.line(qty=Decimal("6"))
    first = await p.wf.invoice(v, po, six)
    await p.push_through(first)
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), six)
    evidence = {"received_value": "100.00", "invoiced_total": "120.00"}
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"second invoice refused with HTTP {ex.status}", **evidence)
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    return breached("120.00 invoiced against 100.00 received", **evidence, downstream=downstream)


@attack(
    "partial_receipt_flag",
    "partial_receipt_flag",
    "Match an invoice while the PO is only partly received",
    "match surfaces the partial receipt instead of silently passing",
    "high",
    "create_invoice",
    "match_invoice",
)
async def partial_receipt_flag(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(), receive=[(p.sku(), Decimal("4"))])
    inv = await p.wf.invoice(v, po, p.line(qty=Decimal("4")))
    ex = await p.wf.match(inv)
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok or surfaces_partial(ex.body) or not match_is_clean(ex.body):
        return held("partial receipt surfaced by match", match=ex.body)
    return breached("match passed cleanly with 4 of 10 units received", match=ex.body)


@attack(
    "inactive_vendor_po",
    "inactive_vendor_gate",
    "Raise a PO against an inactive vendor",
    "PO refused for inactive vendor",
    "high",
    "list_vendors",
)
async def inactive_vendor_po(p: Probe) -> Outcome:
    ex = p.wf.require("list vendors", await p.wf.list_vendors())
    inactive = [v for v in ex.body if pick(v, "active", "is_active", "enabled") is False]
    if inactive:
        vendor_id, how = pick(inactive[0], "id", "vendor_id"), "seeded inactive vendor"
    elif p.wf.catalog.has("update_vendor"):
        vendor_id = await p.wf.fresh_vendor()
        p.wf.require("deactivate vendor", await p.wf.set_vendor_active(vendor_id, False))
        how = "vendor deactivated by the probe"
    else:
        return "SKIPPED", "no inactive vendor and no way to deactivate one", {}
    po = await p.wf.create_po(vendor_id, p.line())
    if po.status >= 500 or po.status == 0:
        return crashed(po)
    if po.ok:
        return breached(f"PO created for inactive vendor {vendor_id} ({how})", po=po.body)
    return held(f"PO refused with HTTP {po.status} ({how})")


@attack(
    "gl_balance_precision",
    "gl_balance",
    "Post 100 x 0.57 (a float trap: 56.99999999999999)",
    "GL entry balances exactly at 57.00 with cent precision",
    "critical",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def gl_balance_precision(p: Probe) -> Outcome:
    lines = p.line(qty=Decimal("100"), price=Decimal("0.57"))
    v = await p.wf.fresh_vendor()
    _, inv = await p.wf.matched_invoice(v, lines)
    ex = p.wf.require("approve invoice", await p.wf.approve(inv))
    entry = pick(ex.body, "gl_entry", "journal_entry", "gl")
    if entry is None and p.wf.catalog.has("gl_entries"):
        gl = await p.wf.gl_for(inv)
        entry = gl.body[0] if gl.ok and isinstance(gl.body, list) and gl.body else None
    if entry is None:
        return "ERROR", "approve succeeded but no GL entry could be read", {}
    problems = gl_problems(entry)
    totals = gl_totals(entry)
    want = expected_amount(lines)
    if dec(totals["credits"]) != want:
        problems.append(f"AP credit {totals['credits']} != expected {want}")
    if problems:
        return breached("; ".join(problems), expected=str(want), **totals, gl_entry=entry)
    return held(f"balanced at {want}", **totals)


@attack(
    "duplicate_invoice_exact",
    "duplicate_invoice_detection",
    "Submit the same invoice_number twice",
    "second invoice refused",
    "critical",
    "create_invoice",
)
async def duplicate_invoice_exact(p: Probe) -> Outcome:
    return await _duplicate(p, lambda n: n)


@attack(
    "duplicate_invoice_normalized",
    "duplicate_invoice_detection",
    "Resubmit the invoice_number in lower case with stray spaces",
    "' inv-x ' treated as a duplicate of 'INV-X'",
    "high",
    "create_invoice",
)
async def duplicate_invoice_normalized(p: Probe) -> Outcome:
    return await _duplicate(p, lambda n: f"  {n.lower()} ")


async def _duplicate(p: Probe, variant: Callable[[str], str]) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    number = p.wf.unique("INV").upper()
    one = p.line(qty=Decimal("1"))
    p.wf.require("first invoice", await p.wf.create_invoice(v, po, number, one))
    again = variant(number)
    ex = await p.wf.create_invoice(v, po, again, one)
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        return breached(f"{again!r} accepted after {number!r}", first=number, second=again)
    return held(f"{again!r} refused with HTTP {ex.status}")


# ---------------------------------------------------------- beyond the brief


@attack(
    "over_receive_single",
    "receipt_quantity",
    "Receive 11 units on a 10-unit PO",
    "receipt above ordered qty refused",
    "high",
)
async def over_receive_single(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(), receive=[])
    ex = await p.wf.receive(po, [(p.sku(), Decimal("11"))])
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        return breached("11 received against 10 ordered", po=ex.body)
    return held(f"receipt refused with HTTP {ex.status}")


@attack(
    "over_receive_cumulative",
    "receipt_quantity",
    "Two receipts of 6 against a 10-unit PO",
    "second receipt refused (6 + 6 > 10)",
    "high",
)
async def over_receive_cumulative(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(), receive=[(p.sku(), Decimal("6"))])
    ex = await p.wf.receive(po, [(p.sku(), Decimal("6"))])
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"second receipt refused with HTTP {ex.status}")
    state = await p.wf.get_po(po)
    return breached("12 received against 10 ordered across two receipts", po=state.body)


@attack(
    "double_approve",
    "exposure_integrity",
    "Approve the same invoice twice",
    "second approval does not post again; exposure unchanged",
    "critical",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
    "vendor_exposure",
)
async def double_approve(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    _, inv = await p.wf.matched_invoice(v, p.line())
    p.wf.require("first approve", await p.wf.approve(inv))
    first = dec(await p.wf.open_ap(v))
    second = await p.wf.approve(inv)
    after = dec(await p.wf.open_ap(v))
    entries = await p.gl_count(inv)
    evidence = {
        "second_approve_status": second.status,
        "open_ap_after_first": str(first),
        "open_ap_after_second": str(after),
        "gl_entries_for_invoice": entries,
    }
    if after != first or (entries is not None and entries > 1):
        return breached(f"open AP went {first} -> {after} on a repeat approval", **evidence)
    return held("repeat approval did not post again", **evidence)


@attack(
    "concurrent_approve",
    "concurrency",
    "Fire two approvals at the same instant",
    "exactly one GL posting",
    "critical",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
    "vendor_exposure",
)
async def concurrent_approve(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    _, inv = await p.wf.matched_invoice(v, p.line())
    a, b = await asyncio.gather(p.wf.approve(inv), p.wf.approve(inv))
    open_ap = dec(await p.wf.open_ap(v))
    entries = await p.gl_count(inv)
    evidence = {
        "statuses": [a.status, b.status],
        "open_ap": str(open_ap),
        "expected_open_ap": "100.00",
        "gl_entries_for_invoice": entries,
    }
    if open_ap != Decimal("100.00") or (entries is not None and entries > 1):
        return breached(f"race posted twice: open AP {open_ap}", **evidence)
    return held("only one approval posted", **evidence)


@attack(
    "cross_vendor_invoice",
    "vendor_consistency",
    "Vendor B invoices against vendor A's PO",
    "invoice refused",
    "critical",
    "create_invoice",
)
async def cross_vendor_invoice(p: Probe) -> Outcome:
    a = await p.wf.fresh_vendor()
    po = await p.wf.received_po(a, p.line())
    b = await p.wf.fresh_vendor()
    ex = await p.wf.create_invoice(b, po, p.wf.unique("INV"), p.line())
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        return breached(f"vendor {b} invoiced vendor {a}'s PO {po}", invoice=ex.body)
    return held(f"refused with HTTP {ex.status}")


@attack(
    "non_positive_values",
    "input_validation",
    "Zero / negative quantities and prices, zero-value invoice",
    "every probe refused",
    "high",
    "create_invoice",
)
async def non_positive_values(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    accepted: list[str] = []
    for label, qty, price in [
        ("PO qty 0", Decimal("0"), PRICE),
        ("PO qty -5", Decimal("-5"), PRICE),
        ("PO price -1.00", TEN, Decimal("-1.00")),
    ]:
        ex = await p.wf.create_po(v, p.line(qty=qty, price=price))
        if ex.ok:
            accepted.append(label)
    po = await p.wf.received_po(v, p.line())
    for label, qty, price in [
        ("invoice qty -1 (disguised credit)", Decimal("-1"), PRICE),
        ("invoice price 0.00 (zero-value)", Decimal("1"), Decimal("0")),
    ]:
        ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line(qty=qty, price=price))
        if ex.ok:
            accepted.append(label)
    if accepted:
        return breached(f"accepted: {', '.join(accepted)}", accepted=accepted)
    return held("all 5 non-positive probes refused")


@attack(
    "price_variance",
    "price_variance",
    "Invoice 5 units at 20.00 on a PO priced at 10.00",
    "match flags the price variance; invoice never reaches the GL",
    "high",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def price_variance(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    ex = await p.wf.create_invoice(
        v, po, p.wf.unique("INV"), p.line(Decimal("5"), Decimal("20.00"))
    )
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"invoice refused at entry with HTTP {ex.status}")
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    if downstream["reached_gl"]:
        return breached(
            "price doubled (10.00 -> 20.00) and the invoice was approved",
            po_price="10.00",
            invoice_price="20.00",
            downstream=downstream,
        )
    return held(f"stopped at {downstream['blocked_at']}", downstream=downstream)


@attack(
    "price_tolerance_overbill",
    "price_variance",
    "Invoice at 10% above PO price (tests whether any tolerance is too wide)",
    "match flags the 10% price variance; invoice never reaches the GL",
    "high",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def price_tolerance_overbill(p: Probe) -> Outcome:
    # PO at PRICE/unit, receive TEN units, invoice 9 units at 10% above PO price.
    # 9 × (PRICE×1.1) = 9 × 11.00 = 99.00 ≤ 100.00 received; passes the overpayment gate.
    # Exact-match check should flag the price variance; a loose tolerance lets it through.
    invoice_price = Decimal("11.00")  # PRICE * 1.10
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line(Decimal("9"), invoice_price))
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"invoice refused at entry with HTTP {ex.status}")
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    if downstream["reached_gl"]:
        return breached(
            f"price 10% over PO ({PRICE} -> {invoice_price}) passed match and was approved",
            po_price=str(PRICE),
            invoice_price=str(invoice_price),
            downstream=downstream,
        )
    return held(f"stopped at {downstream['blocked_at']}", downstream=downstream)


@attack(
    "rematch_after_backorder",
    "state_machine",
    "Re-match a matched_with_exceptions invoice after the backorder fills",
    "second match call returns 409; partial-receipt exception is preserved",
    "medium",
    "create_invoice",
    "match_invoice",
    "receive_po",
)
async def rematch_after_backorder(p: Probe) -> Outcome:
    # Partial receipt (6 of 10) → invoice for 6 → first match gives matched_with_exceptions.
    # Backorder fills (receive remaining 4). Vendor calls match again to clear the exception.
    # Clean API: 409 (invoice is already matched_with_exceptions).
    # Bug: second match succeeds and silently clears the partial-receipt exception.
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(), receive=[(p.sku(), Decimal("6"))])
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line(Decimal("6")))
    if not ex.ok:
        return "ERROR", f"invoice creation failed: HTTP {ex.status}", {"body": ex.body}
    inv_id = entity_id(ex, "invoice_id")
    m1 = await p.wf.match(inv_id)
    if not m1.ok:
        return "ERROR", f"first match failed: HTTP {m1.status}", {"body": m1.body}
    if m1.body.get("status") != "matched_with_exceptions":
        return "SKIPPED", f"expected matched_with_exceptions, got {m1.body.get('status')}", {}
    # Fill the backorder
    p.wf.require("fill backorder", await p.wf.receive(po, [(p.sku(), Decimal("4"))]))
    m2 = await p.wf.match(inv_id)
    if m2.status >= 500 or m2.status == 0:
        return crashed(m2)
    if m2.ok and m2.body.get("status") == "matched":
        return breached(
            "re-match after backorder cleared partial-receipt exception",
            first_match=m1.body,
            second_match=m2.body,
        )
    return held(f"re-match refused with HTTP {m2.status}", first_match=m1.body)


@attack(
    "invoice_extra_sku",
    "match_gate",
    "Invoice a line for a SKU not on the PO (freight / tax charge injection)",
    "invoice refused: the extra SKU is not on the purchase order",
    "high",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def invoice_extra_sku(p: Probe) -> Outcome:
    # PO for sku(0); invoice for sku(0) + sku(1) — sku(1) simulates an off-PO freight line.
    if len(p.skus) < 2:
        return "SKIPPED", "catalogue has fewer than 2 SKUs; cannot build an off-PO line", {}
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line(i=0))
    # Invoice 9 of sku(0) + 1 of sku(1) — total 100 = received, overpayment cap passes.
    # The clean API rejects it because sku(1) is not on the PO.
    main_line: Line = (p.sku(0), Decimal("9"), PRICE)
    extra_line: Line = (p.sku(1), Decimal("1"), PRICE)
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), [main_line, extra_line])
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(
            f"extra-SKU invoice rejected at creation (HTTP {ex.status})",
            extra_sku=p.sku(1),
        )
    # Invoice creation accepted an off-PO SKU — the unauthorized charge is now in
    # the system as a liability, regardless of whether match later catches it.
    return breached(
        f"invoice with off-PO line for {p.sku(1)} accepted at creation (HTTP {ex.status})",
        po_sku=p.sku(0),
        extra_sku=p.sku(1),
        invoice_id=entity_id(ex, "invoice_id"),
    )


@attack(
    "receive_before_submit",
    "state_machine",
    "Receive goods on a draft (unsubmitted) PO",
    "receipt refused until the PO is submitted",
    "medium",
)
async def receive_before_submit(p: Probe) -> Outcome:
    v = await p.wf.fresh_vendor()
    po = entity_id(p.wf.require("create PO", await p.wf.create_po(v, p.line())), "po_id")
    ex = await p.wf.receive(po, [(p.sku(), TEN)])
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        return breached("draft PO accepted a goods receipt", po=ex.body)
    return held(f"refused with HTTP {ex.status}")


DISCRETE_UOMS = {"EA", "EACH", "PC", "PCS", "SHEET", "BOX", "ROLL", "BAG", "PALLET"}


async def _uoms(p: Probe) -> dict[str, str]:
    """sku -> unit of measure, from the catalogue (empty if it publishes none)."""
    if not p.wf.catalog.has("list_items"):
        return {}
    ex = await p.wf.list_items()
    items = ex.body if ex.ok and isinstance(ex.body, list) else []
    found = {pick(i, "sku", "code", "id"): pick(i, "uom", "unit", "unit_of_measure") for i in items}
    return {str(k): str(v).upper() for k, v in found.items() if k and v}


@attack(
    "invoice_uom_mismatch",
    "uom_consistency",
    "Invoice the same qty and price in another unit (LB against a PO in EA)",
    "match flags the unit mismatch; invoice never reaches the GL",
    "high",
    "list_items",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def invoice_uom_mismatch(p: Probe) -> Outcome:
    if not p.wf.catalog.line_field("create_invoice", "uom"):
        return "SKIPPED", "invoice lines take no unit of measure", {}
    uoms = await _uoms(p)
    sku = p.sku()
    ordered = uoms.get(sku)
    if not ordered:
        return "SKIPPED", f"catalogue gives no unit of measure for {sku}", {}
    billed = "LB" if ordered != "LB" else "EA"
    v = await p.wf.fresh_vendor()
    po = await p.wf.received_po(v, p.line())
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line(), uoms=[billed])
    evidence = {"sku": sku, "po_uom": ordered, "invoice_uom": billed}
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"invoice in {billed} refused at entry with HTTP {ex.status}", **evidence)
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    if downstream["reached_gl"]:
        return breached(
            f"10 {billed} matched against 10 {ordered} and approved",
            **evidence,
            downstream=downstream,
        )
    return held(f"stopped at {downstream['blocked_at']}", **evidence, downstream=downstream)


@attack(
    "fractional_discrete_qty",
    "uom_consistency",
    "Order, receive and invoice 2.5 of a discrete unit (EA, BOX, SHEET)",
    "fractional quantity of a discrete unit refused",
    "medium",
    "list_items",
    "create_invoice",
)
async def fractional_discrete_qty(p: Probe) -> Outcome:
    uoms = await _uoms(p)
    discrete = [i for i, s in enumerate(p.skus) if uoms.get(s) in DISCRETE_UOMS]
    if not discrete:
        return "SKIPPED", "no item with a discrete unit of measure in the catalogue", {}
    i = discrete[0]
    sku, half = p.sku(i), Decimal("2.5")
    v = await p.wf.fresh_vendor()
    accepted: list[str] = []
    ex = await p.wf.create_po(v, p.line(qty=half, i=i))
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if ex.ok:
        accepted.append(f"PO for {half} {uoms[sku]}")
    po = await p.wf.received_po(v, p.line(i=i), receive=[])
    ex = await p.wf.receive(po, [(sku, half)])
    if ex.ok:
        accepted.append(f"receipt of {half} {uoms[sku]}")
    elif ex.status < 500:
        p.wf.require("receive goods", await p.wf.receive(po, [(sku, TEN)]))
    ex = await p.wf.create_invoice(v, po, p.wf.unique("INV"), p.line(qty=half, i=i))
    if ex.ok:
        accepted.append(f"invoice for {half} {uoms[sku]}")
    evidence = {"sku": sku, "uom": uoms[sku], "accepted": accepted}
    if accepted:
        return breached(f"accepted: {', '.join(accepted)}", **evidence)
    return held(f"all 3 fractional {uoms[sku]} probes refused", **evidence)


@attack(
    "wrong_uom_conversion_factor",
    "uom_consistency",
    "Invoice 150 LB at half the per-LB price; wrong factor passes both qty and price checks",
    "match fails: 150/50=3 BOX > 2 received; price×50 also ≠ PO price",
    "high",
    "list_items",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)
async def wrong_uom_conversion_factor(p: Probe) -> Outcome:
    uoms = await _uoms(p)
    box_items = [i for i, s in enumerate(p.skus) if uoms.get(s) == "BOX"]
    if not box_items:
        return "SKIPPED", "no BOX item in the catalogue", {}
    if not p.wf.catalog.line_field("create_invoice", "uom"):
        return "SKIPPED", "invoice lines take no unit of measure", {}
    i = box_items[0]
    sku = p.sku(i)
    v = await p.wf.fresh_vendor()
    # PO at PRICE/BOX; receive 2 BOX (= 100 LB at 50 LB/BOX)
    po = await p.wf.received_po(v, p.line(qty=Decimal("2"), i=i))
    # price_lb = PRICE/100: with wrong factor (100) money(price_lb×100)=PRICE ✓ and 150/100=1.5<2 ✓
    # with correct factor (50): money(price_lb×50)=PRICE/2≠PRICE and 150/50=3>2, both fail ✓
    price_lb = PRICE / Decimal("100")
    ex = await p.wf.create_invoice(
        v, po, p.wf.unique("INV"), [(sku, Decimal("150"), price_lb)], uoms=["LB"]
    )
    if ex.status >= 500 or ex.status == 0:
        return crashed(ex)
    if not ex.ok:
        return held(f"invoice in LB refused at creation: HTTP {ex.status}", sku=sku)
    downstream = await p.push_through(entity_id(ex, "invoice_id"))
    evidence = {
        "sku": sku,
        "invoiced_lb": "150",
        "price_per_lb": str(price_lb),
        "received_box": "2",
    }
    if downstream["reached_gl"]:
        return breached(
            "150 LB at half price slipped through with wrong conversion factor",
            **evidence,
            downstream=downstream,
        )
    return held(f"stopped at {downstream['blocked_at']}", **evidence, downstream=downstream)


# ------------------------------------------------------------------- runner


class Adversary:
    def __init__(self, client: ApiClient, catalog: Catalog, tag: str = "adv"):
        self.client = client
        self.catalog = catalog
        self.wf = Workflow(client, catalog, tag)

    async def _skus(self) -> list[str]:
        if not self.catalog.has("list_items"):
            return []
        ex = await self.wf.list_items()
        items = ex.body if ex.ok and isinstance(ex.body, list) else []
        return [s for s in (pick(i, "sku", "code", "id") for i in items) if s]

    async def run(self, only: list[str] | None = None) -> list[AttackResult]:
        skus = await self._skus()
        selected = [a for a in ATTACKS if not only or a.id in only]
        return [await self.run_one(a, skus) for a in selected]

    async def run_one(self, a: Attack, skus: list[str]) -> AttackResult:
        start = len(self.client.log)
        missing = self.catalog.missing(*a.needs)
        if missing:
            status, observed, evidence = "SKIPPED", f"API lacks {missing}", {}
        elif not skus:
            status, observed, evidence = "ERROR", "no SKUs discovered to build orders with", {}
        else:
            try:
                status, observed, evidence = await a.run(Probe(self.wf, skus))
            except SetupError as exc:
                status, observed, evidence = "ERROR", str(exc), {"failed_step": exc.step}
        return AttackResult(
            attack=a.id,
            rule=a.rule,
            title=a.title,
            status=status,
            expected=a.expected,
            observed=observed,
            evidence=evidence,
            exchanges=slim_all(self.client.log[start:]),
            severity=a.severity if status == "BREACHED" else "",
        )
