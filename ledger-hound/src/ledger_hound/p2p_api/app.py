"""Practice Purchase-to-Pay API for a building-materials ERP.

This is the *system under test*, a stand-in for the API the interviewer will
run. It keeps everything in memory, is seeded on start-up, and can have
defects switched on through ``create_app(bugs=...)`` or the ``P2P_BUGS``
environment variable (see ``bugs.py``).

With no bugs switched on, every financial rule is enforced:

* invoice amount (cumulative per PO) <= value of goods received
* approve requires a passed 3-way match
* partial receipts surface as a match exception
* no POs against inactive vendors
* every GL entry balances (Decimal money, 2 dp, half-up)
* duplicate invoice numbers per vendor are rejected (case/space-insensitive)
* invoice lines are matched in the PO's unit of measure; discrete units are whole
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .bugs import parse_bugs

CENT = Decimal("0.01")
# Units that cannot be split: half a stud or a third of a box is a data error.
DISCRETE_UOMS = frozenset({"EA", "SHEET", "BOX", "ROLL"})
# Convertible unit pairs: (invoice_uom, po_uom) -> factor such that 1 po_uom = factor invoice_uom.
# 50 LB = 1 BOX (nails are sold in 50-lb boxes; invoices may arrive in either unit).
UOM_CONVERT: dict[tuple[str, str], Decimal] = {("LB", "BOX"): Decimal("50")}


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- domain state


@dataclass
class Vendor:
    id: int
    name: str
    active: bool = True


@dataclass
class POLine:
    sku: str
    qty: Decimal
    unit_price: Decimal
    received_qty: Decimal = Decimal("0")


@dataclass
class PurchaseOrder:
    id: int
    vendor_id: int
    lines: list[POLine]
    status: str = "draft"  # draft -> submitted -> partially_received -> received


@dataclass
class InvoiceLine:
    sku: str
    qty: Decimal
    unit_price: Decimal
    amount: Any  # Decimal normally; float under the float_money bug
    uom: str = ""


@dataclass
class Invoice:
    id: int
    vendor_id: int
    po_id: int
    invoice_number: str
    lines: list[InvoiceLine]
    amount: Any
    status: str = "pending"  # pending -> matched | matched_with_exceptions | match_failed
    exceptions: list[str] = field(default_factory=list)


@dataclass
class GLEntry:
    id: int
    invoice_id: int
    vendor_id: int
    lines: list[dict[str, Any]]


@dataclass
class Store:
    vendors: dict[int, Vendor] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    pos: dict[int, PurchaseOrder] = field(default_factory=dict)
    invoices: dict[int, Invoice] = field(default_factory=dict)
    gl: list[GLEntry] = field(default_factory=list)
    seq: int = 0

    def next_id(self) -> int:
        self.seq += 1
        return self.seq


SEED_ITEMS = [
    ("2X4-8-SPF", "2x4 x 8' SPF stud", "EA", "4.98"),
    ("OSB-716-4X8", "7/16 OSB sheathing 4x8", "SHEET", "14.25"),
    ("NAIL-16D-50LB", "16d common nails, 50 lb box", "BOX", "89.99"),
    ("TYVEK-9X100", "House wrap 9' x 100'", "ROLL", "189.00"),
    ("SHIM-CEDAR", "Cedar shim", "EA", "0.10"),
]
SEED_VENDORS = [
    ("Cascade Lumber Co", True),
    ("Summit Fasteners", True),
    ("Old Mill Supply", False),
]


def seed(store: Store) -> None:
    for sku, desc, uom, price in SEED_ITEMS:
        store.items[sku] = {"sku": sku, "description": desc, "uom": uom, "list_price": price}
    for name, active in SEED_VENDORS:
        vid = store.next_id()
        store.vendors[vid] = Vendor(vid, name, active)


# ------------------------------------------------------------------ API models
# Money and quantities are accepted as strings or numbers and kept as Decimal.
# Positivity is checked in the handlers (not with Field(gt=0)) so the
# ``non_positive`` bug can switch it off.


class VendorIn(BaseModel):
    name: str = Field(min_length=1)
    active: bool = True


class VendorPatch(BaseModel):
    active: bool


class LineIn(BaseModel):
    sku: str
    qty: Decimal
    unit_price: Decimal


class InvoiceLineIn(LineIn):
    uom: str | None = Field(default=None, description="defaults to the catalogue unit")


class POIn(BaseModel):
    vendor_id: int
    lines: list[LineIn] = Field(min_length=1)


class ReceiveLineIn(BaseModel):
    sku: str
    qty: Decimal


class ReceiveIn(BaseModel):
    lines: list[ReceiveLineIn] = Field(min_length=1)


class InvoiceIn(BaseModel):
    vendor_id: int
    po_id: int
    invoice_number: str = Field(min_length=1)
    lines: list[InvoiceLineIn] = Field(min_length=1)


# --------------------------------------------------------------------- the app


def create_app(bugs: frozenset[str] | None = None) -> FastAPI:
    if bugs is None:
        bugs = parse_bugs(os.environ.get("P2P_BUGS"))
    store = Store()
    seed(store)
    approve_lock = asyncio.Lock()
    float_money = "float_money" in bugs
    # 10% tolerance lets a small price hike slip through match
    price_tolerance = Decimal("0.10") if "relaxed_price_tolerance" in bugs else Decimal("0")

    app = FastAPI(
        title="P2P API (practice)",
        version="1.0.0",
        description="Purchase-to-Pay for a building materials ERP.",
    )
    app.state.store = store
    app.state.bugs = bugs

    # -- helpers ------------------------------------------------------------

    def out(value: Any) -> Any:
        """Serialize money/qty: strings normally, floats under float_money."""
        if isinstance(value, Decimal):
            return float(value) if float_money else str(value)
        return value

    def vendor_or_404(vendor_id: int) -> Vendor:
        if vendor_id not in store.vendors:
            raise HTTPException(404, f"vendor {vendor_id} not found")
        return store.vendors[vendor_id]

    def po_or_404(po_id: int) -> PurchaseOrder:
        if po_id not in store.pos:
            raise HTTPException(404, f"purchase order {po_id} not found")
        return store.pos[po_id]

    def invoice_or_404(invoice_id: int) -> Invoice:
        if invoice_id not in store.invoices:
            raise HTTPException(404, f"invoice {invoice_id} not found")
        return store.invoices[invoice_id]

    def require_positive(field_name: str, value: Decimal) -> None:
        if "non_positive" not in bugs and value <= 0:
            raise HTTPException(422, f"{field_name} must be greater than zero")

    def uom_of(sku: str) -> str:
        return store.items[sku]["uom"]

    def require_whole(sku: str, qty: Decimal) -> None:
        if "fractional_uom" not in bugs and uom_of(sku) in DISCRETE_UOMS and qty % 1:
            raise HTTPException(422, f"qty {qty} of {sku} must be whole ({uom_of(sku)})")

    def uom_factor(from_uom: str, to_uom: str) -> Decimal | None:
        """Return the conversion factor (1 to_uom = factor from_uom), or None if unknown.
        Under wrong_uom_factor the factor is doubled, understating the converted quantity."""
        factor = UOM_CONVERT.get((from_uom, to_uom))
        if factor is None:
            return None
        return factor * 2 if "wrong_uom_factor" in bugs else factor

    def ordered_value(po: PurchaseOrder) -> Decimal:
        return sum((money(ln.qty * ln.unit_price) for ln in po.lines), Decimal("0"))

    def received_value(po: PurchaseOrder) -> Decimal:
        return sum((money(ln.received_qty * ln.unit_price) for ln in po.lines), Decimal("0"))

    def invoiced_value(po: PurchaseOrder) -> Decimal:
        return sum(
            (Decimal(str(inv.amount)) for inv in store.invoices.values() if inv.po_id == po.id),
            Decimal("0"),
        )

    def po_json(po: PurchaseOrder) -> dict[str, Any]:
        return {
            "id": po.id,
            "vendor_id": po.vendor_id,
            "status": po.status,
            "lines": [
                {
                    "sku": ln.sku,
                    "uom": uom_of(ln.sku),
                    "qty": out(ln.qty),
                    "unit_price": out(ln.unit_price),
                    "received_qty": out(ln.received_qty),
                }
                for ln in po.lines
            ],
            "total": out(ordered_value(po)),
            "received_value": out(received_value(po)),
            "fully_received": all(ln.received_qty >= ln.qty for ln in po.lines),
        }

    def invoice_json(inv: Invoice) -> dict[str, Any]:
        return {
            "id": inv.id,
            "vendor_id": inv.vendor_id,
            "po_id": inv.po_id,
            "invoice_number": inv.invoice_number,
            "status": inv.status,
            "exceptions": inv.exceptions,
            "amount": out(inv.amount),
            "lines": [
                {
                    "sku": ln.sku,
                    "uom": ln.uom,
                    "qty": out(ln.qty),
                    "unit_price": out(ln.unit_price),
                    "amount": out(ln.amount),
                }
                for ln in inv.lines
            ],
        }

    def gl_json(entry: GLEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "invoice_id": entry.invoice_id,
            "vendor_id": entry.vendor_id,
            "lines": [{k: out(v) for k, v in ln.items()} for ln in entry.lines],
        }

    # -- vendors and catalogue ---------------------------------------------

    @app.get("/vendors")
    async def list_vendors() -> list[dict[str, Any]]:
        return [{"id": v.id, "name": v.name, "active": v.active} for v in store.vendors.values()]

    @app.post("/vendors", status_code=201)
    async def create_vendor(body: VendorIn) -> dict[str, Any]:
        vid = store.next_id()
        store.vendors[vid] = Vendor(vid, body.name, body.active)
        return {"id": vid, "name": body.name, "active": body.active}

    @app.patch("/vendors/{vendor_id}")
    async def update_vendor(vendor_id: int, body: VendorPatch) -> dict[str, Any]:
        vendor = vendor_or_404(vendor_id)
        vendor.active = body.active
        return {"id": vendor.id, "name": vendor.name, "active": vendor.active}

    @app.get("/items")
    async def list_items() -> list[dict[str, Any]]:
        return list(store.items.values())

    # -- purchase orders ----------------------------------------------------

    @app.post("/purchase-orders", status_code=201)
    async def create_po(body: POIn) -> dict[str, Any]:
        vendor = vendor_or_404(body.vendor_id)
        if not vendor.active and "inactive_po" not in bugs:
            raise HTTPException(422, f"vendor {vendor.id} is inactive")
        skus = [ln.sku for ln in body.lines]
        if len(set(skus)) != len(skus):
            raise HTTPException(422, "duplicate sku on purchase order")
        for ln in body.lines:
            if ln.sku not in store.items:
                raise HTTPException(422, f"unknown sku {ln.sku}")
            require_positive("qty", ln.qty)
            require_positive("unit_price", ln.unit_price)
            require_whole(ln.sku, ln.qty)
        po = PurchaseOrder(
            id=store.next_id(),
            vendor_id=vendor.id,
            lines=[POLine(ln.sku, ln.qty, ln.unit_price) for ln in body.lines],
        )
        store.pos[po.id] = po
        return po_json(po)

    @app.get("/purchase-orders/{po_id}")
    async def get_po(po_id: int) -> dict[str, Any]:
        return po_json(po_or_404(po_id))

    @app.post("/purchase-orders/{po_id}/submit")
    async def submit_po(po_id: int) -> dict[str, Any]:
        po = po_or_404(po_id)
        if po.status != "draft":
            raise HTTPException(409, f"cannot submit a {po.status} purchase order")
        po.status = "submitted"
        return po_json(po)

    @app.post("/purchase-orders/{po_id}/receive")
    async def receive_po(po_id: int, body: ReceiveIn) -> dict[str, Any]:
        po = po_or_404(po_id)
        if po.status not in {"submitted", "partially_received"}:
            raise HTTPException(409, f"cannot receive against a {po.status} purchase order")
        by_sku = {ln.sku: ln for ln in po.lines}
        for rl in body.lines:
            if rl.sku not in by_sku:
                raise HTTPException(422, f"sku {rl.sku} is not on purchase order {po.id}")
            require_positive("qty", rl.qty)
            require_whole(rl.sku, rl.qty)
            line = by_sku[rl.sku]
            already = Decimal("0") if "over_receive_cumulative" in bugs else line.received_qty
            if already + rl.qty > line.qty:
                raise HTTPException(
                    422,
                    f"receiving {rl.qty} of {rl.sku} would exceed ordered qty {line.qty} "
                    f"(already received {line.received_qty})",
                )
        for rl in body.lines:
            by_sku[rl.sku].received_qty += rl.qty
        full = all(ln.received_qty >= ln.qty for ln in po.lines)
        po.status = "received" if full else "partially_received"
        return po_json(po)

    # -- invoices -----------------------------------------------------------

    @app.post("/invoices", status_code=201)
    async def create_invoice(body: InvoiceIn) -> dict[str, Any]:
        vendor_or_404(body.vendor_id)
        po = po_or_404(body.po_id)
        if po.vendor_id != body.vendor_id and "cross_vendor" not in bugs:
            raise HTTPException(422, "invoice vendor does not match purchase order vendor")
        if po.status == "draft":
            raise HTTPException(409, "cannot invoice a draft purchase order")

        def norm(number: str) -> str:
            return number if "dup_case" in bugs else number.strip().upper()

        for inv in store.invoices.values():
            if inv.vendor_id == body.vendor_id and norm(inv.invoice_number) == norm(
                body.invoice_number
            ):
                raise HTTPException(
                    409, f"duplicate invoice_number {body.invoice_number!r} for vendor"
                )

        po_skus = {ln.sku for ln in po.lines}
        lines: list[InvoiceLine] = []
        for ln in body.lines:
            if ln.sku not in po_skus:
                raise HTTPException(422, f"sku {ln.sku} is not on purchase order {po.id}")
            require_positive("qty", ln.qty)
            require_positive("unit_price", ln.unit_price)
            require_whole(ln.sku, ln.qty)
            if float_money:
                amount: Any = float(ln.qty) * float(ln.unit_price)
            else:
                amount = money(ln.qty * ln.unit_price)
            uom = (ln.uom or uom_of(ln.sku)).strip().upper()
            lines.append(InvoiceLine(ln.sku, ln.qty, ln.unit_price, amount, uom))
        total: Any = (
            sum(ln.amount for ln in lines)
            if float_money
            else sum((ln.amount for ln in lines), Decimal("0"))
        )

        cap = ordered_value(po) if "overpay_ordered" in bugs else received_value(po)
        already = Decimal("0") if "overpay_cumulative" in bugs else invoiced_value(po)
        if already + Decimal(str(total)) > cap:
            raise HTTPException(
                422,
                f"invoice amount {money(Decimal(str(total)))} plus already invoiced {already} "
                f"exceeds received value {received_value(po)}",
            )

        inv = Invoice(
            id=store.next_id(),
            vendor_id=body.vendor_id,
            po_id=po.id,
            invoice_number=body.invoice_number,
            lines=lines,
            amount=total,
        )
        store.invoices[inv.id] = inv
        return invoice_json(inv)

    @app.get("/invoices/{invoice_id}")
    async def get_invoice(invoice_id: int) -> dict[str, Any]:
        return invoice_json(invoice_or_404(invoice_id))

    @app.post("/invoices/{invoice_id}/match")
    async def match_invoice(invoice_id: int) -> dict[str, Any]:
        inv = invoice_or_404(invoice_id)
        if inv.status != "pending" and "rematch_allowed" not in bugs:
            raise HTTPException(409, f"invoice is already {inv.status}")
        po = store.pos[inv.po_id]
        by_sku = {ln.sku: ln for ln in po.lines}
        failures: list[str] = []
        for ln in inv.lines:
            po_line = by_sku[ln.sku]
            inv_qty = ln.qty
            conv_factor: Decimal | None = None
            if ln.uom != uom_of(ln.sku) and "uom_ignored" not in bugs:
                conv_factor = uom_factor(ln.uom, uom_of(ln.sku))
                if conv_factor is None:
                    failures.append(f"uom_mismatch:{ln.sku}")
                else:
                    inv_qty = ln.qty / conv_factor
            if inv_qty > po_line.received_qty:
                failures.append(f"qty_exceeds_receipt:{ln.sku}")
            if "no_price_check" not in bugs:
                if conv_factor is not None:
                    # invoice price is per invoice unit; scale to the PO unit before comparing
                    scaled = money(ln.unit_price * conv_factor)
                    variance = abs(scaled - po_line.unit_price) / po_line.unit_price
                    if variance > price_tolerance:
                        failures.append(f"price_variance:{ln.sku}")
                else:
                    variance = abs(ln.unit_price - po_line.unit_price) / po_line.unit_price
                    if variance > price_tolerance:
                        failures.append(f"price_variance:{ln.sku}")
        if failures:
            inv.status, inv.exceptions = "match_failed", failures
        elif not all(ln.received_qty >= ln.qty for ln in po.lines) and (
            "silent_partial" not in bugs
        ):
            inv.status, inv.exceptions = "matched_with_exceptions", ["partial_receipt"]
        else:
            inv.status, inv.exceptions = "matched", []
        return {"invoice_id": inv.id, "status": inv.status, "exceptions": inv.exceptions}

    @app.post("/invoices/{invoice_id}/approve")
    async def approve_invoice(invoice_id: int) -> dict[str, Any]:
        inv = invoice_or_404(invoice_id)

        async def check_and_post() -> dict[str, Any]:
            approvable = {"matched", "matched_with_exceptions"}
            if "unmatched_approve" in bugs:
                approvable |= {"pending", "match_failed"}
            if "double_post" in bugs:
                approvable.add("approved")
            if inv.status not in approvable:
                raise HTTPException(409, f"cannot approve an invoice that is {inv.status}")
            await asyncio.sleep(0.01)  # simulated ledger write latency
            if float_money:
                debits = [
                    {
                        "account": "1400 Inventory/GRNI",
                        "sku": ln.sku,
                        "debit": ln.amount,
                        "credit": 0,
                    }
                    for ln in inv.lines
                ]
                credit: Any = round(inv.amount, 2)
            else:
                debits = [
                    {
                        "account": "1400 Inventory/GRNI",
                        "sku": ln.sku,
                        "debit": ln.amount,
                        "credit": Decimal("0.00"),
                    }
                    for ln in inv.lines
                ]
                credit = inv.amount
            entry = GLEntry(
                id=store.next_id(),
                invoice_id=inv.id,
                vendor_id=inv.vendor_id,
                lines=[
                    *debits,
                    {
                        "account": "2000 Accounts Payable",
                        "debit": Decimal("0.00"),
                        "credit": credit,
                    },
                ],
            )
            store.gl.append(entry)
            inv.status = "approved"
            return {"invoice_id": inv.id, "status": inv.status, "gl_entry": gl_json(entry)}

        if "race_approve" in bugs:
            return await check_and_post()
        async with approve_lock:
            return await check_and_post()

    # -- ledger and exposure -------------------------------------------------

    @app.get("/gl/entries")
    async def list_gl_entries(invoice_id: int | None = None) -> list[dict[str, Any]]:
        return [gl_json(e) for e in store.gl if invoice_id is None or e.invoice_id == invoice_id]

    @app.get("/vendors/{vendor_id}/exposure")
    async def vendor_exposure(vendor_id: int) -> dict[str, Any]:
        vendor_or_404(vendor_id)
        entries = [e for e in store.gl if e.vendor_id == vendor_id]
        open_ap = sum(
            (
                Decimal(str(ln["credit"]))
                for e in entries
                for ln in e.lines
                if ln["account"].startswith("2000")
            ),
            Decimal("0"),
        )
        return {
            "vendor_id": vendor_id,
            "open_ap": out(money(open_ap)),
            "posted_entries": len(entries),
        }

    return app


app = create_app()
