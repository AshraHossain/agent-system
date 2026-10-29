"""Final answers that break an agent's output schema (live models do this; the mock
only under injected faults). BudgetPlugin counts them per agent and replaces an
invalid answer with that agent's failed output; ADK alone would abort the run."""

import json

import pytest
from google.adk.models import LlmResponse
from google.genai import types

from opspilot.adk import agents
from opspilot.adk.mock_policies import failed_output
from opspilot.adk.models import PROSE_ANSWER, ScriptedLlm
from opspilot.adk.plugins import output_problem
from opspilot.adk.runtime import RunFaults
from opspilot.contracts.findings import ReviewResult, TelemetryFinding

VALID = failed_output("telemetry_analyst", "x")  # a schema-valid TelemetryFinding


def _response(*parts, finish=None, **kw):
    content = types.Content(role="model", parts=list(parts)) if parts else None
    return LlmResponse(content=content, finish_reason=finish, **kw)


# --- the check ----------------------------------------------------------------------


def test_tool_calls_and_partial_chunks_are_not_final_answers():
    call = types.Part(function_call=types.FunctionCall(name="set_model_response", args={}))
    assert output_problem(TelemetryFinding, _response(call)) is None
    assert output_problem(TelemetryFinding, _response(types.Part(text="{"), partial=True)) is None


@pytest.mark.parametrize("text", [json.dumps(VALID), f"```json\n{json.dumps(VALID)}\n```"])
def test_json_that_fits_the_schema_passes(text):
    assert output_problem(TelemetryFinding, _response(types.Part(text=text))) is None


def test_prose_is_reported_without_quoting_it():
    problem = output_problem(TelemetryFinding, _response(types.Part(text=PROSE_ANSWER)))
    assert problem.startswith("final answer is not a valid TelemetryFinding: 1 error(s), first:")
    assert "Invalid JSON" in problem and "congested" not in problem


def test_the_wrong_shape_names_the_first_bad_field():
    shape = _response(types.Part(text='{"status": "completed"}'))
    problem = output_problem(TelemetryFinding, shape)
    assert "not a valid TelemetryFinding: 7 error(s), first: summary: Field required" in problem


def test_truncated_blocked_and_empty_answers_say_so():
    cut = _response(types.Part(text=json.dumps(VALID)[:40]), finish=types.FinishReason.MAX_TOKENS)
    assert "TelemetryFinding (finish_reason MAX_TOKENS): 1 error(s)" in output_problem(
        TelemetryFinding, cut
    )
    blocked = _response(finish=types.FinishReason.SAFETY, error_code=types.FinishReason.SAFETY)
    assert output_problem(TelemetryFinding, blocked) == "no answer (SAFETY)"


def test_a_textless_chunk_without_an_error_is_left_to_adk():
    # When streaming, a thought-only chunk can come right before a tool call.
    thought_only = _response(types.Part(text="hmm", thought=True))
    assert output_problem(ReviewResult, thought_only) is None
    assert output_problem(ReviewResult, _response(finish=types.FinishReason.STOP)) is None


# --- in the pipeline ----------------------------------------------------------------


async def test_a_clean_run_records_no_compliance_problems(run_case):
    m = (await run_case("C03")).report.run_metrics
    assert m.schema_retries_by_agent == m.text_answers_by_agent == m.fallbacks_by_agent == {}


async def test_a_prose_answer_fails_only_that_stage(run_case):
    out = await run_case("C03", faults=RunFaults(invalid_output=frozenset({"telemetry_analyst"})))
    report, m = out.report, out.report.run_metrics
    assert out.aborted is None and report.status.value == "inconclusive"
    assert report.specialist_status == {
        "telemetry": "failed",
        "topology": "completed",
        "knowledge": "completed",
        "incident_analysis": "completed",
    }
    (error,) = out.state["telemetry_finding"]["errors"]
    assert error.startswith("invalid output: final answer is not a valid TelemetryFinding")
    assert m.fallbacks_by_agent == {"telemetry_analyst": {"invalid_output": 1}}
    assert m.text_answers_by_agent == {"telemetry_analyst": 1} and m.schema_retries_by_agent == {}


async def test_a_prose_review_does_not_cost_the_investigation(run_case):
    clean = await run_case("C03")
    out = await run_case("C03", faults=RunFaults(invalid_output=frozenset({"review_verifier"})))
    m = out.report.run_metrics
    assert out.aborted is None and out.report.status == clean.report.status
    assert m.fallbacks_by_agent == {"review_verifier": {"invalid_output": 1}}
    assert m.text_answers_by_agent == {}  # without tools, text is the expected channel


async def test_rejected_arguments_are_retried_and_counted(run_case):
    clean = (await run_case("C03")).report
    faults = RunFaults(invalid_args=frozenset({"incident_analyst"}), transient=True)
    out = (await run_case("C03", faults=faults)).report
    m = out.run_metrics
    assert out.status == clean.status and m.fallbacks_by_agent == {}
    assert m.schema_retries_by_agent == {"incident_analyst": 1}
    calls = m.llm_calls_by_agent["incident_analyst"]
    assert calls == clean.run_metrics.llm_calls_by_agent["incident_analyst"] + 1


async def test_arguments_that_never_validate_end_at_the_iteration_budget(run_case, settings):
    clean_calls = (await run_case("C03")).report.run_metrics.llm_calls_by_agent["incident_analyst"]
    out = await run_case("C03", faults=RunFaults(invalid_args=frozenset({"incident_analyst"})))
    m, cap = out.report.run_metrics, settings.limits.max_llm_calls_per_agent
    assert out.report.specialist_status["incident_analysis"] == "failed"
    assert m.fallbacks_by_agent == {"incident_analyst": {"budget": 1}}
    assert m.llm_calls_by_agent["incident_analyst"] == cap
    # Every call after the tool turns was a rejected answer.
    assert m.schema_retries_by_agent == {"incident_analyst": cap - (clean_calls - 1)}


async def test_model_errors_are_counted_where_the_stage_falls_back(run_case):
    out = await run_case("C03", faults=RunFaults(model_timeout=frozenset({"knowledge_researcher"})))
    assert out.report.run_metrics.fallbacks_by_agent == {"knowledge_researcher": {"model_error": 1}}


def test_schema_faults_come_from_case_specs():
    f = RunFaults.from_spec({"invalid_output": ["review_verifier"], "invalid_args": ["x"]})
    assert f.invalid_output == {"review_verifier"} and f.invalid_args == {"x"}


class Rewriting(ScriptedLlm):
    """The mock, with its final `set_model_response` call rewritten the way live models err."""

    mode: str = ""

    async def generate_content_async(self, llm_request, stream=False):
        async for response in super().generate_content_async(llm_request, stream):
            calls = [p.function_call for p in response.content.parts if p.function_call]
            if not (self.mode and calls and calls[0].name == "set_model_response"):
                yield response
            elif self.mode == "json_text":
                text = f"```json\n{json.dumps(calls[0].args)}\n```"
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text=text)])
                )
            else:  # blocked: no content at all
                yield LlmResponse(error_code="SAFETY", finish_reason=types.FinishReason.SAFETY)


def _rewrite(monkeypatch, target: str, mode: str) -> None:
    def make_model(agent, settings, faults=None):
        return Rewriting(agent=agent, mode=mode if agent == target else "")

    monkeypatch.setattr(agents, "make_model", make_model)


async def test_json_sent_as_text_is_accepted_but_counted(run_case, monkeypatch):
    _rewrite(monkeypatch, "telemetry_analyst", "json_text")
    out = await run_case("C03")
    m = out.report.run_metrics
    assert out.report.specialist_status["telemetry"] == "completed"
    assert m.text_answers_by_agent == {"telemetry_analyst": 1} and m.fallbacks_by_agent == {}


async def test_a_blocked_answer_fails_the_stage_with_the_reason(run_case, monkeypatch):
    _rewrite(monkeypatch, "telemetry_analyst", "blocked")
    out = await run_case("C03")
    assert out.aborted is None and out.report.specialist_status["telemetry"] == "failed"
    assert out.state["telemetry_finding"]["errors"] == ["invalid output: no answer (SAFETY)"]
    assert out.report.run_metrics.fallbacks_by_agent == {"telemetry_analyst": {"invalid_output": 1}}
