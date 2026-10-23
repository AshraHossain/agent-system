"""The agent's own exam: every planted bug caught, no false alarms on a clean API."""

from __future__ import annotations

import pytest

from ledger_hound.p2p_api.app import create_app
from ledger_hound.p2p_api.bugs import BUGS
from ledger_hound.qa.adversary import Adversary
from ledger_hound.qa.discovery import discover
from ledger_hound.qa.explorer import Explorer
from ledger_hound.qa.http import ApiClient


async def _statuses(bugs: frozenset[str]) -> tuple[str, dict[str, str]]:
    client = ApiClient.for_app(create_app(bugs))
    catalog = await discover(client)
    happy = await Explorer(client, catalog).run()
    results = await Adversary(client, catalog).run()
    await client.aclose()
    return happy["status"], {r.attack: r.status for r in results}


async def test_clean_api_everything_holds():
    happy, statuses = await _statuses(frozenset())
    assert happy == "PASS"
    assert set(statuses.values()) == {"HELD"}, statuses


@pytest.mark.parametrize("bug", sorted(BUGS))
async def test_planted_bug_is_detected(bug):
    _, statuses = await _statuses(frozenset({bug}))
    assert statuses[BUGS[bug].caught_by] == "BREACHED"


async def test_overpayment_breach_carries_numbers():
    client = ApiClient.for_app(create_app(frozenset({"overpay_ordered"})))
    catalog = await discover(client)
    [result] = await Adversary(client, catalog).run(only=["invoice_exceeds_received"])
    assert result.evidence["received_value"] == "40.00"
    assert result.evidence["invoice_amount"] == "100.00"
    assert result.exchanges, "evidence must include the real requests"
    await client.aclose()


async def test_happy_path_ends_with_exposure_cleared():
    client = ApiClient.for_app(create_app(frozenset()))
    happy = await Explorer(client, await discover(client)).run()
    await client.aclose()
    last = happy["steps"][-1]
    assert [s["name"] for s in happy["steps"][-2:]] == ["pay invoice", "exposure after payment"]
    assert last["verdict"] == "as_expected", last


async def test_exposure_not_cleared_also_fails_happy_path():
    client = ApiClient.for_app(create_app(frozenset({"exposure_not_cleared"})))
    happy = await Explorer(client, await discover(client)).run()
    await client.aclose()
    assert happy["status"] == "FAIL"
    failed = [s["name"] for s in happy["steps"] if s["verdict"] != "as_expected"]
    assert failed == ["exposure after payment"]


async def test_float_bug_also_fails_happy_path():
    # 10 x 4.98 in float is 49.800000000000004 > 49.80 received: a real finding, not planned.
    happy, _ = await _statuses(frozenset({"float_money"}))
    assert happy == "FAIL"
