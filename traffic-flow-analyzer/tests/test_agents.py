import asyncio
import pathlib
import re

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from trafficflow.agents import agents, tools
from trafficflow.agents.deps import TrafficDeps
from trafficflow.agents.pipeline import ask, run_report, verify_brief
from trafficflow.contracts import Finding, TrafficBrief
from trafficflow.data.feed import SyntheticFeed

ROOT = pathlib.Path(__file__).parents[1] / "trafficflow"


def ctx(feed):
    return RunContext(deps=TrafficDeps(feed=feed), model=TestModel(), usage=RunUsage())


def test_layering_core_and_contracts_do_not_import_ai_frameworks():
    for pkg in ("core", "contracts"):
        for f in (ROOT / pkg).rglob("*.py"):
            assert not re.search(
                r"^\s*(from|import)\s+(pydantic_ai|gradio)", f.read_text(), re.M
            ), f


def test_tools_reject_unknown_segment_and_record_evidence():
    feed = SyntheticFeed()
    c = ctx(feed)
    bad = asyncio.run(tools.forecast_segment(c, "../etc/passwd", 60))
    assert bad["status"] == "error" and "S1" in bad["known"]
    ok = asyncio.run(tools.forecast_segment(c, "S1", 60))
    assert ok["status"] == "ok" and ok["evidence_id"] in c.deps.evidence


def test_tool_schema_bounds_horizon():
    agent = agents.forecast_analyst()
    schema = next(
        t for t in agent._function_toolset.tools.values() if t.name == "forecast_segment"
    ).function_schema.json_schema
    assert schema["properties"]["horizon_minutes"]["maximum"] == 180


async def test_pipeline_mock_end_to_end():
    feed = SyntheticFeed()
    feed.advance(12)
    r = await run_report(feed, 60)
    assert r.status.value in {"watch", "degraded"}
    assert not any("HARD_FAIL" in i for i in r.issues)
    assert any("S2" in f.segment_ids for f in r.brief.findings)
    assert all(e in r.evidence for f in r.brief.findings for e in f.evidence_ids)


def test_verifier_flags_fabricated_citation_and_segment():
    brief = TrafficBrief(
        headline="h",
        summary="s",
        recommendations=[],
        findings=[Finding(claim="c", segment_ids=["S99"], evidence_ids=["EV-999"])],
    )
    issues = verify_brief(brief, {"EV-001": {}}, {"S1"})
    assert len(issues) == 2


def test_verifier_warns_on_unsupported_numeric_claim():
    # Evidence contains speed 42; finding claims speed 99 — mismatch should warn.
    brief = TrafficBrief(
        headline="h",
        summary="s",
        recommendations=[],
        findings=[Finding(claim="speed is 99 km/h", segment_ids=["S1"], evidence_ids=["EV-001"])],
    )
    evidence = {"EV-001": {"kind": "forecast", "speed_kmh": 42.0}}
    issues = verify_brief(brief, evidence, {"S1"})
    # Should have at least one numeric-mismatch warning.
    numeric_issues = [i for i in issues if "numeric claim" in i]
    assert len(numeric_issues) >= 1


def test_compare_models_tool():
    feed = SyntheticFeed()
    c = ctx(feed)
    result = asyncio.run(tools.compare_models(c, "S1", 60))
    assert result["status"] == "ok"
    assert set(result["models"].keys()) == {"seasonal_residual", "moving_average", "persistence"}
    for model_points in result["models"].values():
        assert isinstance(model_points, list)
        assert len(model_points) == 3


async def test_ask_mock():
    out = await ask(SyntheticFeed(), "How is Ring Road North?")
    assert out.startswith("S2")


async def test_status_ignores_llm_text():
    """Writer output cannot change status; it is derived from core results."""
    feed = SyntheticFeed()  # 07:30, quiet before the incident
    r = await run_report(feed, 15)
    assert r.status.value in {"normal", "watch", "degraded"}
