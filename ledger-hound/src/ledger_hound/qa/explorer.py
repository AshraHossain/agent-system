"""Exploration agent, scripted mode: walk the happy path end to end.

Given only the discovered catalog, it finds an active vendor and real SKUs,
then runs vendor -> PO -> submit -> receive -> invoice -> match -> approve.
At every step it checks the business result as well as the status code:
the PO total, the received value, a clean match, a balanced GL entry, and
vendor exposure rising by exactly the invoice amount.

The live mode (``agentic.explore_with_agent``) lets the LLM drive the same
journey with tools. Both modes produce the same ``Step`` records, and both
end in the same oracle checks.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .discovery import Catalog
from .http import ApiClient
from .models import Step
from .oracle import dec, expected_amount, gl_problems, match_is_clean, pick
from .workflow import Line, SetupError, Workflow, entity_id

CORE_ROLES = (
    "list_vendors",
    "create_po",
    "submit_po",
    "receive_po",
    "create_invoice",
    "match_invoice",
    "approve_invoice",
)


class Explorer:
    def __init__(self, client: ApiClient, catalog: Catalog, tag: str = "explore"):
        self.wf = Workflow(client, catalog, tag)
        self.catalog = catalog
        self.steps: list[Step] = []

    def _add(self, name: str, expected: str, ex: Any, failures: list[str], observed: str = ""):
        self.steps.append(Step.build(len(self.steps) + 1, name, expected, ex, failures, observed))
        return not failures

    async def run(self) -> dict[str, Any]:
        missing = self.catalog.missing(*CORE_ROLES)
        self._add(
            "discover API",
            "all P2P operations found",
            None,
            [f"missing roles: {missing}"] if missing else [],
            f"{len(self.catalog.operations)} operations via {self.catalog.source}",
        )
        if missing:
            return self._result()
        try:
            await self._journey()
        except _Abort:
            pass
        except SetupError as exc:
            self._add(exc.step, "2xx", exc.exchange, [str(exc)])
        return self._result()

    def _result(self) -> dict[str, Any]:
        passed = all(s.verdict == "as_expected" for s in self.steps)
        return {"status": "PASS" if passed else "FAIL", "steps": [s.to_dict() for s in self.steps]}

    def _must(self, ok: bool) -> None:
        if not ok:
            raise _Abort

    async def _journey(self) -> None:
        wf = self.wf

        # 1. vendor: reuse an active one if the API has it, else create one
        ex = await wf.list_vendors()
        vendors = ex.body if isinstance(ex.body, list) else []
        active = [v for v in vendors if pick(v, "active", "is_active", "enabled") is not False]
        self._must(
            self._add(
                "list vendors",
                "200 with a list of vendors",
                ex,
                [] if ex.ok else ["could not list vendors"],
                f"{len(vendors)} vendors, {len(active)} active",
            )
        )
        if active:
            vendor_id = pick(active[0], "id", "vendor_id")
        else:
            ex = await wf.create_vendor(wf.unique("Explorer Vendor"))
            self._must(
                self._add(
                    "create vendor", "201 vendor created", ex, [] if ex.ok else ["create failed"]
                )
            )
            vendor_id = entity_id(ex, "vendor_id")

        # 2. SKUs: take two real ones from the catalogue
        lines = await self._pick_lines()
        po_value = expected_amount(lines)

        # 3. PO
        ex = await wf.create_po(vendor_id, lines)
        total = dec(pick(ex.body, "total", "amount", "total_amount"))
        fails = [] if ex.ok else ["PO rejected"]
        if ex.ok and total is not None and total != po_value:
            fails.append(f"PO total {total} != expected {po_value}")
        self._must(self._add("create purchase order", f"201, total {po_value}", ex, fails))
        po_id = entity_id(ex, "po_id")

        ex = await wf.submit_po(po_id)
        self._must(
            self._add("submit PO", "2xx, status submitted", ex, [] if ex.ok else ["submit failed"])
        )

        # 4. receive everything
        ex = await wf.receive(po_id, [(s, q) for s, q, _ in lines])
        self._must(
            self._add(
                "receive goods", "2xx, fully received", ex, [] if ex.ok else ["receipt rejected"]
            )
        )

        ex = await wf.get_po(po_id)
        received = dec(pick(ex.body, "received_value"))
        fails = [] if ex.ok else ["could not read PO"]
        if received is not None and received != po_value:
            fails.append(f"received_value {received} != expected {po_value}")
        self._add("read PO", f"received_value {po_value}", ex, fails)

        before = dec(await wf.open_ap(vendor_id)) if self.catalog.has("vendor_exposure") else None

        # 5. invoice for exactly what was received
        ex = await wf.create_invoice(vendor_id, po_id, wf.unique("INV"), lines)
        amount = dec(pick(ex.body, "amount", "total"))
        fails = [] if ex.ok else ["invoice rejected"]
        if ex.ok and amount != po_value:
            fails.append(f"invoice amount {amount} != expected {po_value}")
        self._must(self._add("create invoice", f"201, amount {po_value}", ex, fails))
        inv_id = entity_id(ex, "invoice_id")

        ex = await wf.match(inv_id)
        fails = [] if ex.ok and match_is_clean(ex.body) else ["match did not pass cleanly"]
        self._must(self._add("3-way match", "2xx, matched with no exceptions", ex, fails))

        ex = await wf.approve(inv_id)
        entry = pick(ex.body, "gl_entry", "journal_entry", "gl")
        fails = [] if ex.ok else ["approve rejected"]
        if ex.ok and entry is None and self.catalog.has("gl_entries"):
            gl = await wf.gl_for(inv_id)
            entry = gl.body[0] if gl.ok and isinstance(gl.body, list) and gl.body else None
        if ex.ok:
            fails += gl_problems(entry) if entry else ["no GL entry produced"]
        self._add("approve + post to GL", "2xx, balanced GL entry", ex, fails)

        if before is not None:
            ex = await wf.exposure(vendor_id)
            after = dec(pick(ex.body, "open_ap", "exposure", "total", "amount"))
            delta = (after - before) if after is not None else None
            fails = [] if delta == po_value else [f"exposure moved by {delta}, expected {po_value}"]
            self._add(
                "vendor exposure",
                f"open AP up by exactly {po_value}",
                ex,
                fails,
                f"open AP {before} -> {after}",
            )

    async def _pick_lines(self) -> list[Line]:
        if not self.catalog.has("list_items"):
            self._add("list items", "SKU catalogue", None, ["no items endpoint discovered"])
            raise _Abort
        ex = await self.wf.list_items()
        items = ex.body if isinstance(ex.body, list) else []
        fails = [] if ex.ok and len(items) >= 2 else ["need at least two SKUs"]
        self._must(self._add("list items", "200 with SKUs", ex, fails, f"{len(items)} SKUs"))
        out: list[Line] = []
        for item, qty in zip(items[:2], (Decimal("10"), Decimal("5")), strict=False):
            price = dec(pick(item, "list_price", "price", "unit_price")) or Decimal("10.00")
            out.append((pick(item, "sku", "code", "id"), qty, price))
        return out


class _Abort(Exception):
    """Stop the journey after a step whose failure blocks the rest."""
