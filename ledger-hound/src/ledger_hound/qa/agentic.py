"""Tool-using agents (live mode only).

* ``explore_with_agent``  - the LLM drives the happy path itself: it reads the
  operation list, makes calls, recovers from 4xx errors, and reports the IDs
  it created. The oracle then re-reads those IDs and decides PASS/FAIL.
* ``improvise_attacks``   - the LLM invents probes that are NOT in the library
  and runs them. Its findings are labelled ``source="llm"`` and "unverified",
  because a model's claim is a lead for a human, not a verdict.

Safety: the only tool that touches the API is ``call_api``. It accepts only a
method and path that match an operation found by discovery, and it always
goes to the base URL under test. The model cannot reach other hosts or
invent endpoints.
"""

from __future__ import annotations

import json
from typing import Any

from .brain import LiveBrain, extract_json
from .discovery import Catalog
from .http import ApiClient
from .models import AttackResult, Step, slim_all
from .oracle import dec, gl_problems, pick
from .workflow import Workflow

MAX_TOOL_OUTPUT = 3000


def _tools(client: ApiClient, catalog: Catalog):
    from agents import function_tool

    @function_tool
    def list_operations() -> str:
        """List every API operation discovered from the OpenAPI document, with body fields."""
        return json.dumps(catalog.describe())

    @function_tool
    async def call_api(method: str, path: str, body_json: str = "") -> str:
        """Call the API under test.

        Args:
            method: HTTP method, e.g. GET or POST.
            path: Concrete path such as /purchase-orders/7/submit (no host).
            body_json: JSON request body as a string, or empty for none.
        """
        if not catalog.allows(method, path):
            return json.dumps({"error": f"{method} {path} is not a discovered operation"})
        try:
            body = json.loads(body_json) if body_json.strip() else None
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"body_json is not valid JSON: {exc}"})
        ex = await client.call(method, path, body)
        return json.dumps({"status": ex.status, "body": ex.body}, default=str)[:MAX_TOOL_OUTPUT]

    return [list_operations, call_api]


EXPLORER_INSTRUCTIONS = """\
You are a QA engineer exploring a Purchase-to-Pay (P2P) API you have never seen.
Goal: complete one valid end-to-end flow:
  find (or create) an ACTIVE vendor -> find real SKUs -> create a purchase order
  -> submit it -> receive ALL goods -> create an invoice for exactly the received
  goods at the PO prices -> run 3-way match -> approve.
Start with list_operations. Read error messages and adapt your next call when
something is rejected. Use a unique invoice_number. Do not try to break rules.
When done, reply with JSON only:
{"vendor_id": ..., "po_id": ..., "invoice_id": ..., "notes": "<one paragraph>"}
"""


async def explore_with_agent(
    client: ApiClient, catalog: Catalog, brain: LiveBrain, max_turns: int = 40
) -> dict[str, Any]:
    from agents import Agent, Runner

    agent = Agent(
        name="explorer",
        instructions=EXPLORER_INSTRUCTIONS,
        model=brain.model,
        tools=_tools(client, catalog),
    )
    start = len(client.log)
    result = await Runner.run(agent, "Begin.", max_turns=max_turns)
    claim = extract_json(str(result.final_output))
    calls = client.log[start:]

    # Intermediate rejections are allowed (the agent may probe and recover), so the
    # agent's own calls are logged without a verdict. Only the end-state checks
    # below decide PASS/FAIL, and they come from the oracle.
    steps = [
        Step.build(
            i + 1,
            f"{ex.method} {ex.path}",
            "agent-chosen call",
            ex,
            [],
            f"HTTP {ex.status}" + ("" if ex.ok else " (agent recovered)"),
        )
        for i, ex in enumerate(calls)
    ]
    verify = await verify_end_state(client, catalog, claim)
    steps += [Step.build(len(steps) + 1 + i, n, e, x, f) for i, (n, e, x, f) in enumerate(verify)]
    passed = all(not f for _, _, _, f in verify)
    return {
        "status": "PASS" if passed else "FAIL",
        "mode": "agent",
        "agent_notes": claim.get("notes", ""),
        "recovered_errors": sum(1 for ex in calls if not ex.ok),
        "steps": [s.to_dict() for s in steps],
    }


async def verify_end_state(client: ApiClient, catalog: Catalog, claim: dict[str, Any]):
    """Re-read what the agent says it built and check the books, independently."""
    wf = Workflow(client, catalog, "verify")
    out = []
    po_id, inv_id = claim.get("po_id"), claim.get("invoice_id")
    po = await wf.get_po(po_id)
    received = dec(pick(po.body, "received_value"))
    fails = (
        [] if po.ok and pick(po.body, "fully_received") is not False else ["PO not fully received"]
    )
    out.append(("verify PO", "fully received", po, fails))

    if catalog.has("get_invoice"):
        inv = await wf.client.call("GET", catalog.path("get_invoice", inv_id))
        amount = dec(pick(inv.body, "amount", "total"))
        fails = []
        if pick(inv.body, "status") != "approved":
            fails.append(f"invoice status {pick(inv.body, 'status')!r}, expected 'approved'")
        if received is not None and amount is not None and amount != received:
            fails.append(f"invoice {amount} != received value {received}")
        out.append(("verify invoice", "approved, amount == received value", inv, fails))

    if catalog.has("gl_entries"):
        gl = await wf.gl_for(inv_id)
        entries = gl.body if gl.ok and isinstance(gl.body, list) else []
        fails = [] if len(entries) == 1 else [f"{len(entries)} GL entries for invoice (expected 1)"]
        if entries:
            fails += gl_problems(entries[0])
        out.append(("verify GL", "exactly one balanced entry", gl, fails))
    return out


IMPROVISER_INSTRUCTIONS = """\
You are a red-team auditor attacking a Purchase-to-Pay API. A fixed library of
probes has already run (results below). Invent {n} NEW probes that the library
did not try, aimed at financial-integrity failures: off-by-one quantities,
rounding at half-cents, huge values, unit mix-ups, re-opening approved
documents, receipts after invoicing, and so on. Run each one with call_api,
using fresh vendors so you do not disturb other data.
Reply with JSON only:
{{"findings": [{{"rule": "...", "title": "...", "hypothesis": "...",
   "verdict": "HELD" | "BREACHED", "evidence": "<status codes and key numbers>"}}]}}
"""


async def improvise_attacks(
    client: ApiClient, catalog: Catalog, brain: LiveBrain, library: list[AttackResult], n: int = 3
) -> list[AttackResult]:
    from agents import Agent, Runner

    agent = Agent(
        name="improviser",
        instructions=IMPROVISER_INSTRUCTIONS.format(n=n),
        model=brain.model,
        tools=_tools(client, catalog),
    )
    tried = [{"attack": r.attack, "rule": r.rule, "status": r.status} for r in library]
    start = len(client.log)
    result = await Runner.run(agent, json.dumps({"already_tried": tried}), max_turns=40)
    findings = extract_json(str(result.final_output)).get("findings", [])
    exchanges = slim_all(client.log[start:])
    out: list[AttackResult] = []
    for f in findings[:n]:
        verdict = "BREACHED" if str(f.get("verdict", "")).upper() == "BREACHED" else "HELD"
        out.append(
            AttackResult(
                attack=f"llm:{f.get('title', 'probe')}"[:80],
                rule=str(f.get("rule", "unspecified")),
                title=str(f.get("title", "")),
                status=verdict,
                expected=str(f.get("hypothesis", "")),
                observed=f"UNVERIFIED model claim: {f.get('evidence', '')}",
                exchanges=exchanges,
                source="llm",
            )
        )
    return out
