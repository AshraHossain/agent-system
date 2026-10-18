"""The practice API itself: rules enforced when clean, bug flags parsed strictly."""

from __future__ import annotations

import pytest

from ledger_hound.p2p_api.bugs import BUGS, parse_bugs
from ledger_hound.qa.adversary import ATTACKS


async def test_happy_flow_posts_balanced_gl(clean):
    client, _ = clean
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "SHIM-CEDAR", "qty": "3", "unit_price": "0.10"}]},
    )
    assert po.status == 201 and po.body["total"] == "0.30"
    await client.call("POST", "/purchase-orders/4/submit")
    await client.call(
        "POST", "/purchase-orders/4/receive", {"lines": [{"sku": "SHIM-CEDAR", "qty": "3"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": 4,
            "invoice_number": "A-1",
            "lines": [{"sku": "SHIM-CEDAR", "qty": "3", "unit_price": "0.10"}],
        },
    )
    assert inv.body["amount"] == "0.30"
    assert (await client.call("POST", "/invoices/5/match")).body["status"] == "matched"
    approved = await client.call("POST", "/invoices/5/approve")
    lines = approved.body["gl_entry"]["lines"]
    assert sum(float(x["debit"]) for x in lines) == sum(float(x["credit"]) for x in lines) == 0.30
    exposure = await client.call("GET", "/vendors/1/exposure")
    assert exposure.body["open_ap"] == "0.30"


async def test_inactive_vendor_is_seeded(clean):
    client, _ = clean
    vendors = (await client.call("GET", "/vendors")).body
    assert any(v["active"] is False for v in vendors)


def test_parse_bugs():
    assert parse_bugs("") == frozenset()
    assert parse_bugs("all") == frozenset(BUGS)
    assert parse_bugs(" dup_case , float_money") == {"dup_case", "float_money"}
    with pytest.raises(ValueError, match="unknown"):
        parse_bugs("not_a_bug")


def test_every_bug_names_a_real_attack():
    ids = {a.id for a in ATTACKS}
    assert {b.caught_by for b in BUGS.values()} <= ids


async def _received_po(client, sku: str, qty: str, price: str) -> int:
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": sku, "qty": qty, "unit_price": price}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": sku, "qty": qty}]}
    )
    return pid


async def test_invoice_in_unconvertible_unit_fails_match(clean):
    # ROLL has no conversion to BOX; mismatch must be flagged
    client, catalog = clean
    assert catalog.line_field("create_invoice", "uom")
    assert not catalog.line_field("create_po", "uom")
    pid = await _received_po(client, "NAIL-16D-50LB", "10", "89.99")
    line = {"sku": "NAIL-16D-50LB", "qty": "10", "unit_price": "89.99", "uom": "roll"}
    inv = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "U-1", "lines": [line]},
    )
    assert inv.body["lines"][0]["uom"] == "ROLL"
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    assert match.body == {
        "invoice_id": inv.body["id"],
        "status": "match_failed",
        "exceptions": ["uom_mismatch:NAIL-16D-50LB"],
    }


async def test_invoice_in_convertible_unit_matches(clean):
    # 100 LB = 2 BOX; PO price 89.99/BOX → invoice price 1.7998/LB (89.99/50)
    client, _ = clean
    pid = await _received_po(client, "NAIL-16D-50LB", "2", "89.99")
    line = {"sku": "NAIL-16D-50LB", "qty": "100", "unit_price": "1.7998", "uom": "LB"}
    inv = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "U-LB-1", "lines": [line]},
    )
    assert inv.status == 201
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    assert match.body["status"] == "matched", match.body


async def test_invoice_overbill_in_convertible_unit_fails(clean):
    # 150 LB at 1.7998/LB = 269.97 > 179.98 received → rejected at invoice creation
    # Use a lower price (0.8999/LB) so the amount passes, but 150/50=3 BOX > 2 BOX → fails match
    client, _ = clean
    pid = await _received_po(client, "NAIL-16D-50LB", "2", "89.99")
    # 0.8999/LB × 50 = 44.995 ≠ 89.99/BOX → price_variance also fires; both failures expected
    line = {"sku": "NAIL-16D-50LB", "qty": "150", "unit_price": "0.8999", "uom": "LB"}
    inv = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "U-LB-2", "lines": [line]},
    )
    assert inv.status == 201
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    assert match.body["status"] == "match_failed"
    assert "qty_exceeds_receipt:NAIL-16D-50LB" in match.body["exceptions"]


async def test_unit_defaults_to_catalogue_and_case_is_ignored(clean):
    client, _ = clean
    pid = await _received_po(client, "NAIL-16D-50LB", "2", "89.99")
    line = {"sku": "NAIL-16D-50LB", "qty": "2", "unit_price": "89.99", "uom": " box "}
    inv = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "U-2", "lines": [line]},
    )
    assert (await client.call("POST", f"/invoices/{inv.body['id']}/match")).body[
        "status"
    ] == "matched"
    po = await client.call("GET", f"/purchase-orders/{pid}")
    assert po.body["lines"][0]["uom"] == "BOX"


async def test_fractional_discrete_qty_refused(clean):
    client, _ = clean
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "2.5", "unit_price": "4.98"}]},
    )
    assert po.status == 422 and "whole" in po.body["detail"]
    pid = await _received_po(client, "2X4-8-SPF", "4", "4.98")
    line = {"sku": "2X4-8-SPF", "qty": "2.50", "unit_price": "4.98"}
    inv = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "F-1", "lines": [line]},
    )
    assert inv.status == 422
    whole = await client.call(
        "POST",
        "/invoices",
        {"vendor_id": 1, "po_id": pid, "invoice_number": "F-2", "lines": [line | {"qty": "2.00"}]},
    )
    assert whole.status == 201  # 2.00 is a whole number
