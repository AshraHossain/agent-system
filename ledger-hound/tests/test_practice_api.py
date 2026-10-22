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


async def test_price_variance_fails_match_on_clean_api(clean):
    # PO at 10.00/unit, receive 10, invoice 9 units at 11.00 (9*11=99 <= 100 received)
    # Amount passes overpayment gate but price variance should fail match
    client, _ = clean
    pid = await _received_po(client, "2X4-8-SPF", "10", "10.00")
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "PV-1",
            "lines": [{"sku": "2X4-8-SPF", "qty": "9", "unit_price": "11.00"}],
        },
    )
    assert inv.status == 201, inv.body
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    assert match.body["status"] == "match_failed"
    assert "price_variance:2X4-8-SPF" in match.body["exceptions"]


async def test_relaxed_price_tolerance_lets_ten_pct_through():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    # Same setup but with the relaxed_price_tolerance bug — 10% should pass
    client = ApiClient.for_app(create_app(frozenset({"relaxed_price_tolerance"})))
    pid = await _received_po(client, "2X4-8-SPF", "10", "10.00")
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "PV-2",
            "lines": [{"sku": "2X4-8-SPF", "qty": "9", "unit_price": "11.00"}],
        },
    )
    assert inv.status == 201, inv.body
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    # 10% is exactly within the 10% tolerance; match should pass
    assert match.body["status"] == "matched", match.body
    await client.aclose()


async def _partial_receipt_setup(client, bug: str = "") -> tuple[int, int]:
    """PO for 10 studs, receive 6 only. Returns (po_id, invoice_id)."""
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "10", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "6"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": f"RM{bug}-1",
            "lines": [{"sku": "2X4-8-SPF", "qty": "6", "unit_price": "4.98"}],
        },
    )
    return pid, inv.body["id"]


async def test_rematch_blocked_on_clean_api(clean):
    # Partial receipt → match gives matched_with_exceptions → clean API blocks re-match
    client, _ = clean
    pid, inv_id = await _partial_receipt_setup(client)
    m1 = await client.call("POST", f"/invoices/{inv_id}/match")
    assert m1.body["status"] == "matched_with_exceptions"
    m2 = await client.call("POST", f"/invoices/{inv_id}/match")
    assert m2.status == 409


async def test_rematch_allowed_bug_clears_exception():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"rematch_allowed"})))
    pid, inv_id = await _partial_receipt_setup(client, bug="BUG")
    m1 = await client.call("POST", f"/invoices/{inv_id}/match")
    assert m1.body["status"] == "matched_with_exceptions"
    # Fill the backorder — PO was for 10, received 6, now receive 4 more
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "4"}]}
    )
    m2 = await client.call("POST", f"/invoices/{inv_id}/match")
    assert m2.status == 200
    assert m2.body["status"] == "matched"
    await client.aclose()


async def test_extra_sku_rejected_on_clean_api(clean):
    # PO for 2X4-8-SPF only; invoice that also bills OSB-716-4X8 should be rejected
    client, _ = clean
    pid = await _received_po(client, "2X4-8-SPF", "4", "4.98")
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "EX-1",
            "lines": [
                {"sku": "2X4-8-SPF", "qty": "4", "unit_price": "4.98"},
                {"sku": "OSB-716-4X8", "qty": "2", "unit_price": "14.25"},
            ],
        },
    )
    assert inv.status == 422
    assert "OSB-716-4X8" in inv.body.get("detail", "")


async def test_extra_sku_allowed_bug_posts_to_gl():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"extra_sku_allowed"})))
    # Receive 10 units so the combined invoice total (4×4.98 + 2×14.25 = 48.42) stays
    # within the received value cap (10×4.98 = 49.80).
    pid = await _received_po(client, "2X4-8-SPF", "10", "4.98")
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "EX-2",
            "lines": [
                {"sku": "2X4-8-SPF", "qty": "4", "unit_price": "4.98"},
                {"sku": "OSB-716-4X8", "qty": "2", "unit_price": "14.25"},
            ],
        },
    )
    # Bug lets the extra-SKU invoice be created; the unauthorized charge is in the system.
    assert inv.status == 201, inv.body
    match = await client.call("POST", f"/invoices/{inv.body['id']}/match")
    # OSB line qty (2) has no PO receipt — match should fail on qty_exceeds_receipt
    assert match.body["status"] == "match_failed"
    await client.aclose()


# --- receive_draft_po ---


async def test_receive_on_draft_po_rejected(clean):
    client, _ = clean
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    # PO is in draft (not submitted)
    ex = await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "5"}]}
    )
    assert ex.status == 409
    assert "draft" in ex.body.get("detail", "")


async def test_receive_draft_po_bug_accepted():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"receive_draft_po"})))
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    # Bug: receipt accepted even though PO is still draft
    ex = await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "5"}]}
    )
    assert ex.status == 200, ex.body
    await client.aclose()


# --- inactive_vendor_invoice ---


async def test_invoice_for_inactive_vendor_rejected(clean):
    client, _ = clean
    # Use the seeded inactive vendor (id=2 or whichever is inactive)
    vendors = (await client.call("GET", "/vendors")).body
    assert any(v["active"] is False for v in vendors)
    # Create a PO for a fresh active vendor, then deactivate after
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "5"}]}
    )
    # Deactivate vendor 1 after PO was raised
    await client.call("PATCH", "/vendors/1", {"active": False})
    ex = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "INV-INACT-1",
            "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}],
        },
    )
    assert ex.status == 422
    assert "inactive" in ex.body.get("detail", "")


async def test_inactive_vendor_invoice_bug_accepted():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"inactive_vendor_invoice"})))
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "5"}]}
    )
    # Deactivate vendor; bug skips the check
    await client.call("PATCH", "/vendors/1", {"active": False})
    ex = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "INV-INACT-BUG",
            "lines": [{"sku": "2X4-8-SPF", "qty": "5", "unit_price": "4.98"}],
        },
    )
    assert ex.status == 201, ex.body
    await client.aclose()


# --- approve_skips_gl ---


async def _partial_match_setup(client):
    """Create a partial receipt → invoice → match (returns matched_with_exceptions)."""
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "10", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "6"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "INV-PART",
            "lines": [{"sku": "2X4-8-SPF", "qty": "6", "unit_price": "4.98"}],
        },
    )
    inv_id = inv.body["id"]
    match = await client.call("POST", f"/invoices/{inv_id}/match")
    assert match.body["status"] == "matched_with_exceptions"
    return inv_id


async def test_partial_match_always_creates_gl_entry(clean):
    client, _ = clean
    inv_id = await _partial_match_setup(client)
    approved = await client.call("POST", f"/invoices/{inv_id}/approve")
    assert approved.status == 200
    assert approved.body["gl_entry"] is not None


async def test_approve_skips_gl_bug_no_gl_entry():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"approve_skips_gl"})))
    inv_id = await _partial_match_setup(client)
    approved = await client.call("POST", f"/invoices/{inv_id}/approve")
    assert approved.status == 200
    # Bug: no GL entry created for matched_with_exceptions invoice
    assert approved.body["gl_entry"] is None
    await client.aclose()


# --- payment disbursement ---


async def _approved_invoice(client) -> int:
    """PO 4 studs, receive all, invoice, match, approve. Returns invoice_id."""
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "4", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "4"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": f"PAY-{pid}",
            "lines": [{"sku": "2X4-8-SPF", "qty": "4", "unit_price": "4.98"}],
        },
    )
    inv_id = inv.body["id"]
    await client.call("POST", f"/invoices/{inv_id}/match")
    await client.call("POST", f"/invoices/{inv_id}/approve")
    return inv_id


async def test_happy_pay_posts_disbursement_gl(clean):
    client, _ = clean
    inv_id = await _approved_invoice(client)
    paid = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "19.92"})
    assert paid.status == 200
    assert paid.body["status"] == "paid"
    lines = paid.body["gl_entry"]["lines"]
    total_debit = sum(float(x["debit"]) for x in lines)
    total_credit = sum(float(x["credit"]) for x in lines)
    assert abs(total_debit - total_credit) < 0.001
    assert abs(total_debit - 19.92) < 0.001


async def test_pay_requires_approval(clean):
    client, _ = clean
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "2", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "2"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "PAY-UNAPP-1",
            "lines": [{"sku": "2X4-8-SPF", "qty": "2", "unit_price": "4.98"}],
        },
    )
    inv_id = inv.body["id"]
    ex = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "9.96"})
    assert ex.status == 409
    assert "pending" in ex.body.get("detail", "")


async def test_pay_unapproved_bug_accepted():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"pay_unapproved"})))
    po = await client.call(
        "POST",
        "/purchase-orders",
        {"vendor_id": 1, "lines": [{"sku": "2X4-8-SPF", "qty": "2", "unit_price": "4.98"}]},
    )
    pid = po.body["id"]
    await client.call("POST", f"/purchase-orders/{pid}/submit")
    await client.call(
        "POST", f"/purchase-orders/{pid}/receive", {"lines": [{"sku": "2X4-8-SPF", "qty": "2"}]}
    )
    inv = await client.call(
        "POST",
        "/invoices",
        {
            "vendor_id": 1,
            "po_id": pid,
            "invoice_number": "PAY-UNAPP-BUG",
            "lines": [{"sku": "2X4-8-SPF", "qty": "2", "unit_price": "4.98"}],
        },
    )
    inv_id = inv.body["id"]
    ex = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "9.96"})
    assert ex.status == 200, ex.body
    await client.aclose()


async def test_double_pay_rejected(clean):
    client, _ = clean
    inv_id = await _approved_invoice(client)
    first = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "19.92"})
    assert first.status == 200
    second = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "19.92"})
    assert second.status == 409
    assert "paid" in second.body.get("detail", "")


async def test_double_pay_bug_posts_twice():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"double_pay"})))
    inv_id = await _approved_invoice(client)
    first = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "19.92"})
    assert first.status == 200
    second = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "19.92"})
    assert second.status == 200, second.body
    await client.aclose()


async def test_overpay_rejected(clean):
    client, _ = clean
    inv_id = await _approved_invoice(client)
    ex = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "99.99"})
    assert ex.status == 422
    assert "exceeds" in ex.body.get("detail", "")


async def test_overpay_disbursement_bug_accepted():
    from ledger_hound.p2p_api.app import create_app
    from ledger_hound.qa.http import ApiClient

    client = ApiClient.for_app(create_app(frozenset({"overpay_disbursement"})))
    inv_id = await _approved_invoice(client)
    ex = await client.call("POST", f"/invoices/{inv_id}/pay", {"amount": "99.99"})
    assert ex.status == 200, ex.body
    await client.aclose()
