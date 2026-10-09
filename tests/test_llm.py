"""Phase 7: LLM adapter contracts (no model server needed)."""

import json

import pytest
from graph_helpers import run_until, shared_deps

from netpulse.config import Settings
from netpulse.graph.nodes import generation_context
from netpulse.llm.base import GenerationError
from netpulse.llm.fallback import FallbackGenerator
from netpulse.llm.heuristic import HeuristicGenerator
from netpulse.llm.ollama import OllamaGenerator, OllamaUnavailableError
from netpulse.llm.parsing import output_json_schema, parse_output
from netpulse.llm.prompts import build_messages
from netpulse.llm.scripted import ScriptedGenerator

GOOD = {
    "hypotheses": [
        {
            "description": "Inferred congestion on the core uplink",
            "cause_category": "link_congestion",
            "suspected_root_entity": "link-core-1-agg-1",
            "supporting_evidence": ["ev-anom-0001"],
            "confidence_rationale": "detector evidence",
        }
    ]
}


@pytest.fixture(scope="module")
def ctx():
    return generation_context(run_until("prompt_injection", "generate_hypotheses"))


# --- parsing --------------------------------------------------------------------


def test_parse_assigns_ids_and_provenance():
    (h,) = parse_output(json.dumps(GOOD))
    assert h.hypothesis_id == "hyp-01" and h.generated_by == "llm"


def test_parse_accepts_code_fences():
    assert parse_output("```json\n" + json.dumps(GOOD) + "\n```")


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        json.dumps({"hypotheses": [{**GOOD["hypotheses"][0], "cause_category": "aliens"}]}),
        json.dumps({"hypotheses": [{**GOOD["hypotheses"][0], "confidence": 0.97}]}),  # numeric confidence forbidden
        json.dumps({"hypotheses": [GOOD["hypotheses"][0]] * 6}),  # too many
        json.dumps({"answer": "core-1"}),
        "[]",
    ],
)
def test_parse_rejects_invalid_output(raw):
    with pytest.raises(GenerationError):
        parse_output(raw)


def test_output_schema_has_no_confidence_or_id_fields():
    props = output_json_schema()["$defs"]["LLMHypothesis"]["properties"]
    assert not {"confidence", "probability", "hypothesis_id", "generated_by"} & set(props)


# --- prompts --------------------------------------------------------------------


def test_prompt_lists_evidence_ids_and_quarantines_untrusted_text(ctx):
    system, human = build_messages(ctx)
    assert "Do NOT write any numbers" in system[1] and "untrusted_document" in system[1]
    trusted = [e for e in ctx.evidence if e.trusted]
    untrusted = [e for e in ctx.evidence if not e.trusted]
    assert all(f"[{e.evidence_id}]" in human[1] for e in trusted)
    assert untrusted and all(f'id="{e.evidence_id}" trust="untrusted"' in human[1] for e in untrusted)
    for e in untrusted:  # untrusted ids are never listed as plain trusted lines
        assert f"[{e.evidence_id}]" not in human[1]


def test_prompt_carries_verifier_feedback(ctx):
    ctx2 = ctx.model_copy(update={"verifier_feedback": ["hyp-01: unknown_evidence_ref: ev-x"], "attempt": 1})
    assert "PREVIOUS ATTEMPT 1 WAS REJECTED" in build_messages(ctx2)[1][1]


# --- adapters -------------------------------------------------------------------


def test_ollama_generator_uses_injected_model_and_parses(ctx):
    seen = {}

    def fake_invoke(messages):
        seen["messages"] = messages
        return json.dumps(GOOD)

    gen = OllamaGenerator(Settings(), invoke=fake_invoke)
    result = gen.generate(ctx)
    assert result.provider == "ollama" and result.model == "qwen2.5:7b-instruct"
    assert len(result.hypotheses) == 1 and seen["messages"][0][0] == "system"


def test_ollama_generator_propagates_bad_output(ctx):
    with pytest.raises(GenerationError):
        OllamaGenerator(Settings(), invoke=lambda m: "{oops").generate(ctx)


def test_fallback_is_used_only_when_ollama_is_unreachable_and_is_labelled(ctx):
    def down(messages):
        raise OllamaUnavailableError("connection refused")

    heuristic = HeuristicGenerator(shared_deps().topology)
    result = FallbackGenerator(OllamaGenerator(Settings(), invoke=down), heuristic).generate(ctx)
    assert result.provider == "heuristic (fallback)" and "FALLBACK" in result.raw_output
    with pytest.raises(GenerationError):  # malformed output is NOT masked by the fallback
        FallbackGenerator(OllamaGenerator(Settings(), invoke=lambda m: "{oops"), heuristic).generate(ctx)


def test_real_ollama_client_maps_connection_refused(ctx):
    gen = OllamaGenerator(Settings(ollama_url="http://127.0.0.1:9"), timeout_seconds=2)
    with pytest.raises(OllamaUnavailableError):
        gen.generate(ctx)


def test_scripted_generator_sequence(ctx):
    gen = ScriptedGenerator(["garbage", RuntimeError("boom"), GOOD["hypotheses"], lambda c: GOOD])
    with pytest.raises(GenerationError):
        gen.generate(ctx)
    with pytest.raises(RuntimeError):
        gen.generate(ctx)
    assert len(gen.generate(ctx).hypotheses) == 1
    assert len(gen.generate(ctx).hypotheses) == 1
    assert len(gen.generate(ctx).hypotheses) == 1  # last step repeats
    assert len(gen.calls) == 5
