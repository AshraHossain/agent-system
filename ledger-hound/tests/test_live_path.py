"""The live (LLM) code path, driven by a scripted fake OpenAI-compatible endpoint.

No API key or network is needed. These tests check the plumbing: the Agents SDK
in Chat Completions mode, the tool guard, JSON parsing with retry, and the
fallback to the mock brain. They do not check model quality.
"""

from __future__ import annotations

import json

from conftest import live_brain, text, tool

from ledger_hound.qa.agentic import explore_with_agent
from ledger_hound.qa.brain import MockBrain, extract_json
from ledger_hound.qa.judge import investigate


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}


async def test_plan_keeps_every_attack_and_drops_unknown_ids():
    brain, _ = live_brain([text('{"order": ["b", "zzz"], "rationale": "b moves money"}')])
    plan = await brain.plan([{"id": "a", "severity": "high"}, {"id": "b", "severity": "high"}], [])
    assert plan == {"order": ["b", "a"], "rationale": "b moves money"}


async def test_invalid_json_retries_then_falls_back_to_mock():
    brain, seen = live_brain([text("not json"), text("still not json")])
    summary = await brain.summarize(
        {
            "happy_path": "PASS",
            "total": 1,
            "counts": {"BREACHED": 0, "HELD": 1, "ERROR": 0, "SKIPPED": 0},
            "breached_rules": [],
        }
    )
    assert len(seen) == 2, "one retry"
    assert "invalid" in seen[1]["messages"][-1]["content"]
    assert summary.startswith("Happy path PASS")
    assert brain.notes and "fell back to mock" in brain.notes[0]


async def test_agent_explorer_drives_api_and_oracle_grades_it(clean):
    client, catalog = clean
    line = {"sku": "2X4-8-SPF", "qty": "10", "unit_price": "4.98"}
    brain, seen = live_brain(
        [
            tool("list_operations"),
            tool("call_api", method="DELETE", path="/vendors/1", body_json=""),  # guard test
            tool("call_api", method="GET", path="/vendors", body_json=""),
            tool(
                "call_api",
                method="POST",
                path="/purchase-orders",
                body_json=json.dumps({"vendor_id": 1, "lines": [line]}),
            ),
            tool("call_api", method="POST", path="/purchase-orders/4/submit", body_json=""),
            tool(
                "call_api",
                method="POST",
                path="/purchase-orders/4/receive",
                body_json=json.dumps({"lines": [{"sku": "2X4-8-SPF", "qty": "10"}]}),
            ),
            tool(
                "call_api",
                method="POST",
                path="/invoices",
                body_json=json.dumps(
                    {"vendor_id": 1, "po_id": 4, "invoice_number": "AG-1", "lines": [line]}
                ),
            ),
            tool("call_api", method="POST", path="/invoices/5/match", body_json=""),
            tool("call_api", method="POST", path="/invoices/5/approve", body_json=""),
            text('{"vendor_id": 1, "po_id": 4, "invoice_id": 5, "notes": "done"}'),
        ]
    )
    result = await explore_with_agent(client, catalog, brain)

    assert result["status"] == "PASS", result["steps"][-3:]
    assert result["mode"] == "agent"
    tool_results = [m["content"] for m in seen[2]["messages"] if m.get("role") == "tool"]
    assert any("not a discovered operation" in t for t in tool_results)
    assert not any(ex.method == "DELETE" for ex in client.log), "guard must block the call"
    names = [s["name"] for s in result["steps"]]
    assert names[-3:] == ["verify PO", "verify invoice", "verify GL"]


async def test_agent_explorer_lie_is_caught(clean):
    """If the agent claims success but never approved, the oracle says FAIL."""
    client, catalog = clean
    brain, _ = live_brain([text('{"vendor_id": 1, "po_id": 999, "invoice_id": 998}')])
    result = await explore_with_agent(client, catalog, brain)
    assert result["status"] == "FAIL"


async def test_full_investigation_with_mock_brain(clean):
    client, _ = clean
    report = await investigate(client, MockBrain(), target="test")
    assert report["happy_path"]["status"] == "PASS"
    assert report["counts"]["BREACHED"] == 0
    first = report["adversarial"][0]
    assert list(first)[:3] == ["rule", "status", "evidence"]  # challenge's JSON shape
    assert set(report["rules"]) >= {
        "overpayment_protection",
        "match_gate",
        "partial_receipt_flag",
        "inactive_vendor_gate",
        "gl_balance",
        "duplicate_invoice_detection",
    }
