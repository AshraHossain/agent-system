"""Business-level verbs over the discovered API.

The explorer and the adversary both talk in terms of "create a PO" or
"receive 4 units", not URLs. This class translates those steps into calls,
using the roles from discovery and renaming fields to match the API's schema.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .discovery import Catalog
from .http import ApiClient, Exchange
from .oracle import pick

Line = tuple[str, Decimal, Decimal]  # sku, qty, unit_price


class SetupError(Exception):
    """A precondition step failed, so the probe could not be attempted."""

    def __init__(self, step: str, exchange: Exchange | None):
        self.step = step
        self.exchange = exchange
        detail = f"HTTP {exchange.status}: {exchange.body}" if exchange else "role not discovered"
        super().__init__(f"setup step '{step}' failed ({detail})")


def entity_id(ex: Exchange, *extra: str) -> Any:
    return pick(ex.body, "id", *extra)


class Workflow:
    def __init__(self, client: ApiClient, catalog: Catalog, tag: str = "lh"):
        self.client = client
        self.catalog = catalog
        self.tag = tag
        self._counter = 0

    def unique(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self.tag}-{self._counter}"

    async def _call(self, role: str, *ids: Any, body: Any = None) -> Exchange:
        if role not in self.catalog.roles:
            raise SetupError(role, None)
        payload = self.catalog.adapt(role, body) if body is not None else None
        return await self.client.call(
            self.catalog.method(role), self.catalog.path(role, *ids), payload
        )

    # -- raw verbs (return the Exchange, never raise on HTTP errors) --------

    async def list_vendors(self) -> Exchange:
        return await self._call("list_vendors")

    async def list_items(self) -> Exchange:
        return await self._call("list_items")

    async def create_vendor(self, name: str, active: bool = True) -> Exchange:
        return await self._call("create_vendor", body={"name": name, "active": active})

    async def set_vendor_active(self, vendor_id: Any, active: bool) -> Exchange:
        return await self._call("update_vendor", vendor_id, body={"active": active})

    async def create_po(self, vendor_id: Any, lines: list[Line]) -> Exchange:
        body = {
            "vendor_id": vendor_id,
            "lines": [{"sku": s, "qty": str(q), "unit_price": str(p)} for s, q, p in lines],
        }
        return await self._call("create_po", body=body)

    async def submit_po(self, po_id: Any) -> Exchange:
        return await self._call("submit_po", po_id)

    async def receive(self, po_id: Any, lines: list[tuple[str, Decimal]]) -> Exchange:
        body = {"lines": [{"sku": s, "qty": str(q)} for s, q in lines]}
        return await self._call("receive_po", po_id, body=body)

    async def get_po(self, po_id: Any) -> Exchange:
        return await self._call("get_po", po_id)

    async def create_invoice(
        self,
        vendor_id: Any,
        po_id: Any,
        number: str,
        lines: list[Line],
        uoms: list[str] | None = None,
    ) -> Exchange:
        """``uoms`` sets a unit per line; omitted, the API's default (the PO unit) applies."""
        body_lines: list[dict[str, Any]] = [
            {"sku": s, "qty": str(q), "unit_price": str(p)} for s, q, p in lines
        ]
        for line, uom in zip(body_lines, uoms or [], strict=False):
            line["uom"] = uom
        body = {
            "vendor_id": vendor_id,
            "po_id": po_id,
            "invoice_number": number,
            "lines": body_lines,
        }
        return await self._call("create_invoice", body=body)

    async def match(self, invoice_id: Any) -> Exchange:
        return await self._call("match_invoice", invoice_id)

    async def approve(self, invoice_id: Any) -> Exchange:
        return await self._call("approve_invoice", invoice_id)

    async def exposure(self, vendor_id: Any) -> Exchange:
        return await self._call("vendor_exposure", vendor_id)

    async def gl_for(self, invoice_id: Any) -> Exchange:
        role = "gl_entries"
        if role not in self.catalog.roles:
            raise SetupError(role, None)
        path = f"{self.catalog.path(role)}?invoice_id={invoice_id}"
        return await self.client.call("GET", path)

    # -- setup helpers (raise SetupError so probes fail loudly, not silently) --

    @staticmethod
    def require(step: str, ex: Exchange) -> Exchange:
        if not ex.ok:
            raise SetupError(step, ex)
        return ex

    async def fresh_vendor(self) -> Any:
        ex = self.require("create vendor", await self.create_vendor(self.unique("QA Vendor")))
        return entity_id(ex, "vendor_id")

    async def received_po(
        self, vendor_id: Any, lines: list[Line], receive: list[tuple[str, Decimal]] | None = None
    ) -> Any:
        """Create + submit a PO and record receipts (default: receive everything)."""
        po_id = entity_id(
            self.require("create PO", await self.create_po(vendor_id, lines)), "po_id"
        )
        self.require("submit PO", await self.submit_po(po_id))
        receipts = receive if receive is not None else [(s, q) for s, q, _ in lines]
        if receipts:
            self.require("receive goods", await self.receive(po_id, receipts))
        return po_id

    async def invoice(self, vendor_id: Any, po_id: Any, lines: list[Line]) -> Any:
        ex = await self.create_invoice(vendor_id, po_id, self.unique("INV"), lines)
        return entity_id(self.require("create invoice", ex), "invoice_id")

    async def matched_invoice(self, vendor_id: Any, lines: list[Line]) -> tuple[Any, Any]:
        """Fully received PO with an invoice that has passed matching."""
        po_id = await self.received_po(vendor_id, lines)
        inv_id = await self.invoice(vendor_id, po_id, lines)
        self.require("match invoice", await self.match(inv_id))
        return po_id, inv_id

    async def open_ap(self, vendor_id: Any) -> Any:
        ex = self.require("read exposure", await self.exposure(vendor_id))
        return pick(ex.body, "open_ap", "exposure", "total", "amount", "open_liability")
