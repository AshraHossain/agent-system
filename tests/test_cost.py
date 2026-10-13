"""Cost estimation from recorded tokens and a configurable price table."""

import asyncio
import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models import LlmResponse
from google.genai import types

from opspilot import cli
from opspilot.adk import models as M
from opspilot.adk.deterministic import metrics_from_counters
from opspilot.adk.models import ScriptedLlm
from opspilot.adk.plugins import BudgetPlugin, RunCounters
from opspilot.adk.runtime import RunFaults
from opspilot.config import Limits, Settings
from opspilot.contracts.report import InvestigationStatus as S
from opspilot.core.cost import (
    ModelPrice,
    PriceTable,
    billable_tokens,
    estimate_cost,
    load_price_table,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "prices.example.json"
MODEL = "scripted-mock"
TABLE = PriceTable(
    currency="USD",
    models={
        "gemini-2.5-flash": ModelPrice(input_per_mtok=0.30, output_per_mtok=2.50),
        MODEL: ModelPrice(input_per_mtok=1.0, output_per_mtok=4.0),
    },
)


def _usage(prompt=1000, completion=100, total=1100):
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt, candidates_token_count=completion, total_token_count=total
    )


@pytest.fixture
def with_usage(monkeypatch):
    """Make the scripted mock report 1000 prompt + 100 completion tokens per model call."""
    original = M.to_response

    def attach(action, has_tools):
        response = original(action, has_tools)
        return LlmResponse(content=response.content, usage_metadata=_usage())

    monkeypatch.setattr(M, "to_response", attach)


def _priced(settings):
    return replace(settings, prices=TABLE)


# --- pure estimation ---------------------------------------------------------------


def test_cost_is_tokens_times_per_million_price():
    est = estimate_cost(
        TABLE, "gemini-2.5-flash", prompt_tokens=2_000_000, completion_tokens=500_000
    )
    assert est.currency == "USD" and (est.input_tokens, est.output_tokens) == (2_000_000, 500_000)
    assert est.total == pytest.approx(2 * 0.30 + 0.5 * 2.50)


def test_thinking_tokens_beyond_prompt_and_completion_bill_as_output():
    assert billable_tokens(1000, 100, 1500) == (1000, 500)
    est = estimate_cost(TABLE, MODEL, prompt_tokens=1000, completion_tokens=100, total_tokens=1500)
    assert est.total == pytest.approx((1000 * 1.0 + 500 * 4.0) / 1e6)


def test_total_never_lowers_the_bill():
    assert billable_tokens(1000, 100, 900) == (1000, 100)
    assert billable_tokens(1000, 100, None) == (1000, 100)


def test_partial_usage_bills_what_was_reported():
    assert billable_tokens(1000, None, None) == (1000, 0)
    assert billable_tokens(None, 50, None) == (0, 50)


@pytest.mark.parametrize(
    ("table", "model", "tokens"),
    [
        (None, MODEL, (1000, 100)),  # no price table configured
        (TABLE, "some-other-model", (1000, 100)),  # model not in the table
        (TABLE, MODEL, (None, None)),  # provider reported no usage (the mock)
    ],
)
def test_no_estimate_instead_of_a_guess(table, model, tokens):
    assert estimate_cost(table, model, prompt_tokens=tokens[0], completion_tokens=tokens[1]) is None


# --- price table loading ----------------------------------------------------------


def test_example_price_table_is_valid():
    table = load_price_table(EXAMPLE)
    assert table.currency == "USD" and table.price_for("gemini-2.5-flash") is not None


def _write(tmp_path, content):
    path = tmp_path / "prices.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        ({"currency": "USD", "models": {}}, "at least one model"),
        ({"currency": "USD"}, "invalid price table"),
        (
            {"currency": "USD", "models": {"m": {"input_per_mtok": -1, "output_per_mtok": 1}}},
            "greater than or equal to 0",
        ),
        (
            {
                "currency": "USD",
                "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1}},
                "x": 1,
            },
            "invalid price table",
        ),
        (
            {"currency": "USD", "models": {"m": {"input_per_mtok": 1}}},
            "output_per_mtok",
        ),
    ],
)
def test_invalid_price_tables_are_rejected(tmp_path, content, message):
    with pytest.raises(ValueError, match=message):
        load_price_table(_write(tmp_path, content))


def test_missing_price_table_file_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="cannot read price table"):
        load_price_table(tmp_path / "absent.json")


def test_settings_load_the_table_named_by_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("OPSPILOT_PRICE_TABLE", raising=False)
    assert Settings.from_env().prices is None
    monkeypatch.setenv("OPSPILOT_PRICE_TABLE", str(EXAMPLE))
    assert Settings.from_env().prices == load_price_table(EXAMPLE)
    monkeypatch.setenv("OPSPILOT_PRICE_TABLE", str(tmp_path / "absent.json"))
    with pytest.raises(ValueError, match="cannot read price table"):
        Settings.from_env()


# --- token accounting to metrics ---------------------------------------------------


async def test_plugin_accumulates_usage_across_calls_and_per_agent():
    plugin = BudgetPlugin(Limits(), {})
    calls = [
        ("telemetry_analyst", _usage(1000, 100, 1100)),
        ("incident_analyst", _usage(2000, 50, 2250)),
        ("telemetry_analyst", _usage(500, 25, 525)),
    ]
    for agent, usage in calls:
        ctx = SimpleNamespace(invocation_id="inv", agent_name=agent)
        await plugin.after_model_callback(
            callback_context=ctx, llm_response=LlmResponse(usage_metadata=usage)
        )
    c = plugin.counters("inv")
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens) == (3500, 175, 3875)
    assert c.usage_by_agent["telemetry_analyst"] == {
        "prompt_tokens": 1500,
        "completion_tokens": 125,
        "total_tokens": 1625,
    }
    assert c.usage_by_agent["incident_analyst"]["total_tokens"] == 2250


def test_metrics_carry_the_estimate(settings):
    c = RunCounters(prompt_tokens=3000, completion_tokens=150, total_tokens=3350)
    live = replace(settings, provider="gemini", model="gemini-2.5-flash", prices=TABLE)
    m = metrics_from_counters(c, live, {})
    # 200 tokens of thinking sit between prompt+completion and the reported total.
    assert m.cost_currency == "USD"
    assert m.estimated_cost == pytest.approx((3000 * 0.30 + 350 * 2.50) / 1e6, abs=1e-6)


def test_unpriced_model_warns_and_leaves_cost_empty(settings, caplog):
    c = RunCounters(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    live = replace(settings, provider="gemini", model="unlisted-model", prices=TABLE)
    with caplog.at_level(logging.WARNING, logger="opspilot.run"):
        m = metrics_from_counters(c, live, {})
    assert m.estimated_cost is None and m.cost_currency is None
    assert "no price for model unlisted-model" in caplog.text


def test_no_warning_when_no_table_is_configured(settings, caplog):
    c = RunCounters(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    with caplog.at_level(logging.WARNING, logger="opspilot.run"):
        m = metrics_from_counters(c, replace(settings, provider="gemini"), {})
    assert m.estimated_cost is None and not caplog.records


# --- end to end ---------------------------------------------------------------------


async def test_mock_run_has_no_cost_even_with_a_table(run_case, settings):
    m = (await run_case("C02", settings_override=_priced(settings))).report.run_metrics
    assert m.total_tokens is None and m.estimated_cost is None


async def test_run_reports_cost_from_recorded_tokens(run_case, settings, with_usage):
    out = await run_case("C02", settings_override=_priced(settings))
    m = out.report.run_metrics
    assert m.prompt_tokens == 1000 * m.llm_calls and m.completion_tokens == 100 * m.llm_calls
    assert m.estimated_cost == pytest.approx(m.llm_calls * (1000 * 1.0 + 100 * 4.0) / 1e6)
    assert m.cost_currency == "USD"


async def test_aborted_run_still_reports_the_tokens_it_spent(
    run_case, settings, with_usage, monkeypatch
):
    original = ScriptedLlm.generate_content_async

    async def slow(self, llm_request, stream=False):
        await asyncio.sleep(0.4)
        async for r in original(self, llm_request, stream):
            yield r

    monkeypatch.setattr(ScriptedLlm, "generate_content_async", slow)
    priced = replace(_priced(settings), limits=replace(settings.limits, max_duration_s=1.0))
    out = await run_case("C02", settings_override=priced)
    m = out.report.run_metrics
    assert out.aborted and out.report.status == S.FAILED
    assert m.total_tokens and m.estimated_cost and m.estimated_cost > 0


def _counters_for_two_agents():
    c = RunCounters(prompt_tokens=3000, completion_tokens=150, total_tokens=3350)
    c.usage_by_agent["telemetry_analyst"] = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "total_tokens": 1250,  # 200 thinking tokens
    }
    c.usage_by_agent["incident_analyst"] = {
        "prompt_tokens": 2000,
        "completion_tokens": 100,
        "total_tokens": 2100,
    }
    return c


def test_metrics_split_tokens_and_cost_by_agent(settings):
    live = replace(settings, provider="gemini", model="gemini-2.5-flash", prices=TABLE)
    m = metrics_from_counters(_counters_for_two_agents(), live, {})
    assert m.tokens_by_agent["telemetry_analyst"].total_tokens == 1250
    assert m.tokens_by_agent["incident_analyst"].prompt_tokens == 2000
    # Thinking tokens are billed as output per agent, exactly as for the run.
    assert m.cost_by_agent["telemetry_analyst"] == pytest.approx((1000 * 0.30 + 250 * 2.50) / 1e6)
    assert m.cost_by_agent["incident_analyst"] == pytest.approx((2000 * 0.30 + 100 * 2.50) / 1e6)
    assert sum(m.cost_by_agent.values()) == pytest.approx(m.estimated_cost, abs=1e-6)


def test_tokens_by_agent_are_reported_without_a_price_table(settings):
    live = replace(settings, provider="gemini", prices=None)
    m = metrics_from_counters(_counters_for_two_agents(), live, {})
    assert set(m.tokens_by_agent) == {"telemetry_analyst", "incident_analyst"}
    assert m.cost_by_agent == {} and m.estimated_cost is None


def test_agents_without_usage_are_absent(settings):
    c = RunCounters(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    c.llm_by_agent["telemetry_analyst"] = 1
    m = metrics_from_counters(c, replace(settings, prices=TABLE), {})
    assert m.tokens_by_agent == {} and m.cost_by_agent == {}


async def test_run_attributes_cost_to_the_agents_that_called_the_model(
    run_case, settings, with_usage
):
    m = (await run_case("C02", settings_override=_priced(settings))).report.run_metrics
    assert set(m.cost_by_agent) == set(m.llm_calls_by_agent)
    for agent, calls in m.llm_calls_by_agent.items():
        assert m.tokens_by_agent[agent].prompt_tokens == 1000 * calls
        assert m.cost_by_agent[agent] == pytest.approx(calls * (1000 * 1.0 + 100 * 4.0) / 1e6)
    assert sum(m.cost_by_agent.values()) == pytest.approx(m.estimated_cost, abs=1e-5)


async def test_retried_stage_cost_accumulates_on_its_agent(run_case, settings, with_usage):
    two_rounds = replace(
        _priced(settings), limits=replace(settings.limits, max_investigation_rounds=2)
    )
    out = await run_case(
        "C11",
        settings_override=two_rounds,
        faults=RunFaults(model_timeout=frozenset({"incident_analyst"}), transient=True),
    )
    m = out.report.run_metrics
    assert m.retried_stages == ["incident_analyst"]
    # The failed first attempt made no billable call; only the retry did.
    assert m.tokens_by_agent["incident_analyst"].prompt_tokens == 1000 * (
        m.llm_calls_by_agent["incident_analyst"] - 1
    )


async def test_aborted_run_keeps_per_agent_usage(run_case, settings, with_usage, monkeypatch):
    original = ScriptedLlm.generate_content_async

    async def slow(self, llm_request, stream=False):
        await asyncio.sleep(0.4)
        async for r in original(self, llm_request, stream):
            yield r

    monkeypatch.setattr(ScriptedLlm, "generate_content_async", slow)
    priced = replace(_priced(settings), limits=replace(settings.limits, max_duration_s=1.0))
    m = (await run_case("C02", settings_override=priced)).report.run_metrics
    assert m.cost_by_agent and sum(m.cost_by_agent.values()) == pytest.approx(
        m.estimated_cost, abs=1e-5
    )


# --- presentation -------------------------------------------------------------------


async def test_cli_summary_shows_cost(run_case, settings, with_usage, capsys):
    cli.print_outcome(await run_case("C02", settings_override=_priced(settings)))
    assert re.search(r"est\. cost=\d+\.\d{4} USD", capsys.readouterr().out)


async def test_cli_summary_without_cost_says_na(run_case, capsys):
    cli.print_outcome(await run_case("C02"))
    assert "est. cost=n/a" in capsys.readouterr().out


async def test_eval_aggregates_cost(settings, with_usage):
    from opspilot.eval.harness import run_eval

    results = await run_eval(_priced(settings), ["C01", "C02"])
    agg = results["aggregate"]
    assert agg["cost_currency"] == "USD" and agg["cost_total"] > 0
    assert agg["cost_total"] == pytest.approx(sum(c["estimated_cost"] for c in results["cases"]))
    assert agg["cost_per_case_mean"] == pytest.approx(agg["cost_total"] / 2)


async def test_cli_summary_ranks_agents_by_cost(run_case, settings, with_usage, capsys):
    out = await run_case("C02", settings_override=_priced(settings))
    cli.print_outcome(out)
    text = " ".join(capsys.readouterr().out.split())
    assert "cost by agent:" in text
    ranked = sorted(out.report.run_metrics.cost_by_agent.items(), key=lambda kv: -kv[1])
    line = text.split("cost by agent:")[1].split("session:")[0]
    positions = [line.index(agent) for agent, _ in ranked]
    assert positions == sorted(positions)


async def test_cli_summary_has_no_agent_line_without_cost(run_case, capsys):
    cli.print_outcome(await run_case("C02"))
    assert "cost by agent" not in capsys.readouterr().out


async def test_eval_sums_cost_by_agent_and_renders_a_table(settings, with_usage):
    from opspilot.eval.harness import run_eval, to_markdown

    results = await run_eval(_priced(settings), ["C01", "C02"])
    by_agent = results["aggregate"]["cost_by_agent"]
    for agent, total in by_agent.items():
        assert total == pytest.approx(
            sum(c["cost_by_agent"].get(agent, 0) for c in results["cases"])
        )
    assert sum(by_agent.values()) == pytest.approx(results["aggregate"]["cost_total"], abs=1e-5)
    assert list(by_agent.values()) == sorted(by_agent.values(), reverse=True)
    md = to_markdown(results)
    assert "| Agent | Cost (USD) |" in md and "| cost_by_agent |" not in md
    assert all(f"| {agent} |" in md for agent in by_agent)


async def test_eval_without_usage_has_no_agent_cost(settings):
    from opspilot.eval.harness import run_eval, to_markdown

    results = await run_eval(_priced(settings), ["C01"])
    assert results["aggregate"]["cost_by_agent"] is None
    assert "| Agent | Cost" not in to_markdown(results)
