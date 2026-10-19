"""The LLM's jobs, behind one interface with a deterministic mock.

What the model does here:

* ``plan``      - rank the attack library by financial risk for this API
* ``annotate``  - one-line interpretation of each happy-path step
* ``explain``   - business impact of each breach, in an auditor's words
* ``summarize`` - the report's executive summary

What it never does is decide pass/fail. The oracle does that.

``LiveBrain`` talks to any OpenAI-compatible endpoint (Bynara here) through
the OpenAI Agents SDK in Chat Completions mode. Every call asks for JSON,
checks it with Pydantic, and falls back to ``MockBrain`` on any failure, so
a flaky model or a down router costs prose, never a crashed run.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_env(cls) -> LLMSettings | None:
        key = os.environ.get("BYNARA_API_KEY", "").strip()
        if not key:
            return None
        return cls(
            api_key=key,
            base_url=os.environ.get("BYNARA_BASE_URL", "https://router.bynara.id/v1"),
            model=os.environ.get("LEDGER_HOUND_MODEL", "openai/laguna-s-2.1"),
        )


class Brain(Protocol):
    name: str
    notes: list[str]

    async def plan(
        self, attacks: list[dict[str, Any]], catalog: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    async def annotate(self, steps: list[dict[str, Any]]) -> list[str]: ...
    async def explain(self, breaches: list[dict[str, Any]]) -> list[str]: ...
    async def summarize(self, facts: dict[str, Any]) -> str: ...
    async def explain_diff(self, diff: dict[str, Any]) -> str: ...


# --------------------------------------------------------------------- mock


@dataclass
class MockBrain:
    name: str = "mock"
    notes: list[str] = field(default_factory=list)

    async def plan(self, attacks, catalog):
        order = sorted(attacks, key=lambda a: (SEVERITY_ORDER.get(a["severity"], 9), a["id"]))
        return {
            "order": [a["id"] for a in order],
            "rationale": "Money-moving rules first (critical), then data-integrity, then hygiene.",
        }

    async def annotate(self, steps):
        good = "outcome matches the business expectation"
        return [
            f"{s['name']}: "
            + (good if s["verdict"] == "as_expected" else f"unexpected outcome: {s['observed']}")
            for s in steps
        ]

    async def explain(self, breaches):
        return [
            _IMPACT.get(b["rule"], "Financial control bypassed; review before release.")
            for b in breaches
        ]

    async def summarize(self, facts):
        c = facts["counts"]
        worst = ", ".join(facts["breached_rules"]) or "none"
        return (
            f"Happy path {facts['happy_path']}. {c['BREACHED']} of {facts['total']} adversarial "
            f"probes breached a control ({c['HELD']} held, {c['ERROR']} errored, "
            f"{c['SKIPPED']} skipped). Rules breached: {worst}."
        )

    async def explain_diff(self, diff: dict[str, Any]) -> str:
        if diff.get("unchanged"):
            return "No API changes since the previous run."
        parts: list[str] = []
        if diff.get("operations_added"):
            parts.append(f"{len(diff['operations_added'])} operation(s) added")
        if diff.get("operations_removed"):
            parts.append(f"{len(diff['operations_removed'])} operation(s) removed")
        if diff.get("changed"):
            parts.append(f"{len(diff['changed'])} operation(s) changed")
        breaking = diff.get("breaking") or []
        if breaking:
            sample = "; ".join(breaking[:3])
            suffix = f" ({len(breaking) - 3} more)" if len(breaking) > 3 else ""
            parts.append(f"{len(breaking)} breaking change(s): {sample}{suffix}")
        return "API changed since the previous run: " + ", ".join(parts) + "."


_IMPACT = {
    "overpayment_protection": "The company can be billed, and pay, for goods it never received.",
    "match_gate": "Invoices reach the ledger without proof of order and delivery: the core AP fraud control is off.",
    "partial_receipt_flag": "Short shipments pass silently; AP cannot chase the vendor for missing goods.",
    "inactive_vendor_gate": "Spend can flow to vendors that were deliberately shut off (fraud, compliance, insolvency).",
    "gl_balance": "The general ledger no longer balances; financial statements are wrong at the cent level and drift with volume.",
    "duplicate_invoice_detection": "The same bill can be paid twice; this is the most common AP leakage pattern.",
    "receipt_quantity": "Inventory is overstated, and the inflated receipt then justifies an inflated invoice.",
    "exposure_integrity": "Liability is double-counted; cash planning and the vendor's statement disagree.",
    "concurrency": "Two clicks or a retry can post the same liability twice.",
    "vendor_consistency": "One vendor can bill against another vendor's order and redirect payment.",
    "input_validation": "Negative or zero lines create hidden credits and corrupt totals.",
    "price_variance": "A vendor can raise prices after the PO and still be paid in full.",
    "state_machine": "Process steps can be skipped, so approvals and audit trails stop meaning anything.",
    "uom_consistency": "Quantities in different units are compared as equal, so receipts, invoices and inventory stop agreeing on what was bought.",
}


# --------------------------------------------------------------------- live


class _Plan(BaseModel):
    order: list[str]
    rationale: str


class _Lines(BaseModel):
    items: list[str]


class _Summary(BaseModel):
    summary: str


class _DiffExplanation(BaseModel):
    explanation: str


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(candidate)


class LiveBrain:
    def __init__(self, settings: LLMSettings, http_client: Any = None):
        from agents import OpenAIChatCompletionsModel, set_tracing_disabled
        from openai import AsyncOpenAI

        set_tracing_disabled(True)  # traces would go to OpenAI, which needs an OpenAI key
        self.name = f"live:{settings.model}"
        self.notes: list[str] = []
        # http_client is injectable so tests can drive this class against a fake endpoint.
        self.client = AsyncOpenAI(
            base_url=settings.base_url, api_key=settings.api_key, http_client=http_client
        )
        self.model = OpenAIChatCompletionsModel(model=settings.model, openai_client=self.client)
        self._mock = MockBrain()

    async def _ask(
        self, role: str, instructions: str, payload: Any, schema: type[BaseModel]
    ) -> BaseModel:
        from agents import Agent, Runner

        agent = Agent(
            name=role,
            instructions=instructions
            + "\nReply with a single JSON object only, matching: "
            + json.dumps(schema.model_json_schema()),
            model=self.model,
        )
        message = json.dumps(payload, default=str)
        last_error = ""
        for _ in range(2):  # one retry, telling the model what was wrong
            prompt = (
                message
                if not last_error
                else f"{message}\n\nYour last reply was invalid: {last_error}"
            )
            result = await Runner.run(agent, prompt, max_turns=2)
            try:
                return schema.model_validate(extract_json(str(result.final_output)))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:300]
        raise ValueError(f"{role}: no valid JSON after retry ({last_error})")

    async def _safe(self, label: str, live, fallback):
        try:
            return await live()
        except Exception as exc:  # noqa: BLE001 - any model failure degrades to the mock
            self.notes.append(f"{label}: fell back to mock ({type(exc).__name__}: {exc})"[:300])
            return await fallback()

    async def plan(self, attacks, catalog):
        known = [a["id"] for a in attacks]

        async def live():
            out = await self._ask(
                "planner",
                "You are a senior AP/ERP auditor planning an adversarial test of a Purchase-to-Pay "
                "API. Order the attack ids from highest to lowest financial risk for THIS API "
                "(look at its operations). Use only the given ids.",
                {"attacks": attacks, "operations": catalog},
                _Plan,
            )
            order = [i for i in out.order if i in known]
            order += [i for i in known if i not in order]  # never drop a probe
            return {"order": order, "rationale": out.rationale}

        return await self._safe("plan", live, lambda: self._mock.plan(attacks, catalog))

    async def annotate(self, steps):
        async def live():
            out = await self._ask(
                "step-interpreter",
                "For each P2P workflow step, write one sentence interpreting whether the outcome is "
                "correct from an accounting point of view. Return exactly one item per step, in order.",
                [{k: s[k] for k in ("name", "expected", "verdict", "observed")} for s in steps],
                _Lines,
            )
            if len(out.items) != len(steps):
                raise ValueError("wrong number of annotations")
            return out.items

        return await self._safe("annotate", live, lambda: self._mock.annotate(steps))

    async def explain(self, breaches):
        if not breaches:
            return []

        async def live():
            out = await self._ask(
                "breach-analyst",
                "Each item is a confirmed control breach in an AP system, with evidence. For each, "
                "explain the business impact in one or two sentences for a CFO: who can exploit "
                "it and what money is at risk. Do not dispute the finding. One item per breach, in order.",
                [{k: b[k] for k in ("rule", "title", "observed", "evidence")} for b in breaches],
                _Lines,
            )
            if len(out.items) != len(breaches):
                raise ValueError("wrong number of explanations")
            return out.items

        return await self._safe("explain", live, lambda: self._mock.explain(breaches))

    async def summarize(self, facts):
        async def live():
            out = await self._ask(
                "judge-summary",
                "Write a 3-5 sentence executive summary of this QA run for an engineering lead. "
                "Lead with the most dangerous breach. Use only the facts given.",
                facts,
                _Summary,
            )
            return out.summary

        return await self._safe("summarize", live, lambda: self._mock.summarize(facts))

    async def explain_diff(self, diff: dict[str, Any]) -> str:
        async def live():
            out = await self._ask(
                "api-change-analyst",
                "You are reviewing API changes between two runs of a financial-integrity QA tool "
                "for a Purchase-to-Pay system. Summarise what changed and explain the likely impact "
                "on the controls being tested: a removed operation means a probe will be skipped; "
                "a new required field changes how attacks are constructed; a changed type may break "
                "existing probes. Write 2-4 sentences for a senior engineer. Be specific about "
                "breaking changes.",
                diff,
                _DiffExplanation,
            )
            return out.explanation

        return await self._safe("explain_diff", live, lambda: self._mock.explain_diff(diff))


def make_brain(mock: bool = False) -> Brain:
    settings = None if mock else LLMSettings.from_env()
    return LiveBrain(settings) if settings else MockBrain()
