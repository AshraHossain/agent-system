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
