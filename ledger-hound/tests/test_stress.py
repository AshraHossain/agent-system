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


@pytest.mark.parametrize(
    ("bug", "rule"),
    [
        ("overpay_ordered", "overpayment_protection"),
        ("silent_partial", "partial_receipt_flag"),
        ("no_price_check", "price_variance"),
        ("float_money", "gl_balance"),
    ],
)
async def test_bug_shows_up_as_failure_rate(bug, rule):
    result = await _stress(frozenset({bug}))
    assert result["by_rule"].get(rule, 0) > 0
    assert 0 < result["failure_rate"] < 1


async def test_random_volume_cannot_find_targeted_bugs():
    """Duplicate-number bugs need a targeted probe; stress alone stays green. Both are needed."""
    result = await _stress(frozenset({"dup_case"}), n=20)
    assert result["failed"] == 0
