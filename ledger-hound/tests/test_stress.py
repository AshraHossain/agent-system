"""Synthetic stress test: reproducible generation, a correct oracle, real failure rates."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ledger_hound.p2p_api.app import create_app
from ledger_hound.qa.brain import MockBrain
from ledger_hound.qa.discovery import discover
from ledger_hound.qa.http import ApiClient
from ledger_hound.qa.stress import (
    VARIANTS,
    Scenario,
    _candidates,
    digest,
    expect,
    generate,
    shrink_scenario,
    stress,
)

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


async def test_run_tag_scopes_generated_names():
    client = ApiClient.for_app(create_app(frozenset()))
    try:
        result = await stress(client, await discover(client), n=3, seed=7, run_tag="zz9")
    finally:
        await client.aclose()
    names = [r["exchanges"][0]["request"]["body"]["name"] for r in result["results"]]
    assert len(names) == 3 and all("-zz9-" in n for n in names), names


async def test_concurrency_must_be_positive():
    client = ApiClient.for_app(create_app(frozenset()))
    try:
        with pytest.raises(ValueError, match="concurrency"):
            await stress(client, await discover(client), concurrency=0)
    finally:
        await client.aclose()


# --- shrinking and narrative ---------------------------------------------------


def _units(s: Scenario) -> Decimal:
    return sum(
        (q for _, q, _ in s.po_lines),
        sum((q for _, q in s.received), sum((q for _, q, _ in s.invoice_lines), D(0))),
    )


def test_shrink_candidates_are_strictly_smaller_and_consistent():
    for s in generate(30, 5, SKUS):
        for c in _candidates(s):
            assert len(c.po_lines) < len(s.po_lines) or _units(c) < _units(s)
            ordered = {sku: qty for sku, qty, _ in c.po_lines}
            assert all(0 < got <= ordered[sku] for sku, got in c.received)
            assert all(sku in ordered and q > 0 for sku, q, _ in c.invoice_lines)
            assert {sku: p for sku, _, p in c.po_lines}.items() <= {
                sku: p for sku, _, p in s.po_lines
            }.items(), "prices are never changed"


async def test_shrink_reduces_a_failure_to_its_smallest_form():
    """pay_unapproved needs an invoice that is created but stopped at match: 2 in, 1 billed."""
    client = ApiClient.for_app(create_app(frozenset({"pay_unapproved"})))
    try:
        result = await stress(client, await discover(client), n=50, seed=7, shrink=1)
    finally:
        await client.aclose()
    [m] = result["shrunk"]
    assert m["reproduced"] and m["rule"] == "payment_gate"
    assert m["size"]["minimal"] == {"lines": 1, "units": 2}
    [(sku, qty, price)] = m["minimal"]["po_lines"]
    assert m["minimal"]["received"] == [[sku, "2"]]
    assert m["minimal"]["invoice_lines"] == [[sku, "1", str(D(price) * 2)]]


async def test_shrink_takes_one_scenario_per_rule_first():
    client = ApiClient.for_app(create_app(frozenset({"pay_unapproved", "silent_partial"})))
    try:
        result = await stress(client, await discover(client), n=50, seed=7, shrink=2)
    finally:
        await client.aclose()
    assert sorted(m["rule"] for m in result["shrunk"]) == ["partial_receipt_flag", "payment_gate"]


async def test_shrink_reports_a_failure_that_does_not_reproduce():
    client = ApiClient.for_app(create_app(frozenset()))
    try:
        s = generate(1, 7, ["2X4-8-SPF"])[0]
        m = await shrink_scenario(client, await discover(client), s, "payment_gate")
    finally:
        await client.aclose()
    assert m == {"id": 1, "rule": "payment_gate", "reproduced": False, "runs": 1}


async def test_mock_narrative_names_rule_variant_and_reproduction():
    client = ApiClient.for_app(create_app(frozenset({"pay_unapproved"})))
    try:
        result = await stress(client, await discover(client), n=50, seed=7, shrink=1)
    finally:
        await client.aclose()
    facts = digest(result)
    assert "exchanges" not in str(facts), "the narrator gets facts, not transcripts"
    text = await MockBrain().narrate_stress(facts)
    assert "payment_gate" in text and "price_up_qty_down variant (11/11 failed)" in text
    assert "Smallest reproduction for payment_gate: PO 2 x" in text


async def test_mock_narrative_on_a_clean_run():
    text = await MockBrain().narrate_stress(digest(await _stress(frozenset(), n=10)))
    assert text == "All 10 scenarios held every control."
