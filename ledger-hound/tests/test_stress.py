"""Synthetic stress test: reproducible generation, a correct oracle, real failure rates."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ledger_hound.p2p_api.app import create_app
from ledger_hound.qa.discovery import discover
from ledger_hound.qa.http import ApiClient
from ledger_hound.qa.stress import VARIANTS, Scenario, expect, generate, stress

SKUS = ["A", "B", "C", "D", "E"]
D = Decimal


def test_same_seed_same_scenarios():
    assert generate(20, 3, SKUS) == generate(20, 3, SKUS)
    assert generate(20, 3, SKUS) != generate(20, 4, SKUS)


def test_generated_scenarios_are_well_formed():
    scenarios = generate(200, 11, SKUS)
    assert {s.variant for s in scenarios} == set(VARIANTS)
    for s in scenarios:
        ordered = {sku: qty for sku, qty, _ in s.po_lines}
        assert s.received, "first line always has a receipt"
        assert all(0 < got <= ordered[sku] for sku, got in s.received)
        assert all(qty > 0 for _, qty, _ in s.invoice_lines)


@pytest.mark.parametrize(
    ("invoice", "refuse", "reaches_gl", "flag_partial"),
    [
        ([("A", D(4), D("0.57"))], False, True, True),  # exact, but PO only partly received
        ([("A", D(5), D("0.57"))], True, False, False),  # one unit over
        ([("A", D(4), D("0.58"))], True, False, False),  # one cent over
        ([("A", D(2), D("1.14"))], False, False, False),  # total fits, price doubled
    ],
)
def test_expectation(invoice, refuse, reaches_gl, flag_partial):
    s = Scenario(1, "x", [("A", D(10), D("0.57"))], [("A", D(4))], invoice)
    e = expect(s)
    assert e.received_value == D("2.28")
    assert (e.refuse_at_create, e.reaches_gl, e.flag_partial) == (refuse, reaches_gl, flag_partial)


async def _stress(bugs: frozenset[str], n: int = 50) -> dict:
    client = ApiClient.for_app(create_app(bugs))
    try:
        return await stress(client, await discover(client), n=n, seed=7)
    finally:
        await client.aclose()


async def test_clean_api_has_zero_violations():
    result = await _stress(frozenset())
    assert result["failed"] == 0, [r["violations"] for r in result["results"] if r["violations"]]
    assert sum(row["scenarios"] for row in result["by_variant"].values()) == 50


async def test_clean_api_pays_approved_invoices_and_refuses_the_rest():
    result = await _stress(frozenset())
    tried = [r["observed"] for r in result["results"] if "pay_status" in r["observed"]]
    paid = [o for o in tried if o["reached_gl"]]
    stopped = [o for o in tried if not o["reached_gl"]]
    assert paid and stopped
    assert all(o["pay_status"] == 200 and o["open_ap_after_pay"] == "0.00" for o in paid)
    assert all(o["pay_status"] == 409 for o in stopped)


@pytest.mark.parametrize(
    ("bug", "rule"),
    [
        ("overpay_ordered", "overpayment_protection"),
        ("silent_partial", "partial_receipt_flag"),
        ("no_price_check", "price_variance"),
        ("float_money", "gl_balance"),
        ("pay_unapproved", "payment_gate"),
        ("exposure_not_cleared", "exposure_integrity"),
        # paying a liability that approve never booked drives open AP negative
        ("approve_skips_gl", "exposure_integrity"),
    ],
)
async def test_bug_shows_up_as_failure_rate(bug, rule):
    result = await _stress(frozenset({bug}))
    assert result["by_rule"].get(rule, 0) > 0
    assert 0 < result["failure_rate"] < 1


@pytest.mark.parametrize("bug", ["dup_case", "double_pay", "overpay_disbursement"])
async def test_random_volume_cannot_find_targeted_bugs(bug):
    """These need a targeted probe (a repeat or an inflated payment); stress alone stays green."""
    result = await _stress(frozenset({bug}), n=20)
    assert result["failed"] == 0


async def test_concurrency_changes_load_not_outcome():
    """Scenarios are isolated, so running 8 at once must give the same per-scenario verdicts."""
    client = ApiClient.for_app(create_app(frozenset({"overpay_ordered"})))
    try:
        catalog = await discover(client)
        seq = await stress(client, catalog, n=30, seed=7)
        par = await stress(client, catalog, n=30, seed=7, concurrency=8)
    finally:
        await client.aclose()

    def verdicts(r: dict) -> list:
        return [(x["id"], sorted(v["rule"] for v in x["violations"])) for x in r["results"]]

    assert verdicts(par) == verdicts(seq) and par["failed"] > 0
    assert (seq["load"]["concurrency"], par["load"]["concurrency"]) == (1, 8)
    assert par["load"]["http_calls"] == seq["load"]["http_calls"]
    # each scenario keeps its own transcript, even when interleaved
    for row in par["results"]:
        assert row["exchanges"][0]["request"]["path"].endswith("/vendors")


async def test_concurrency_must_be_positive():
    client = ApiClient.for_app(create_app(frozenset()))
    try:
        with pytest.raises(ValueError, match="concurrency"):
            await stress(client, await discover(client), concurrency=0)
    finally:
        await client.aclose()
