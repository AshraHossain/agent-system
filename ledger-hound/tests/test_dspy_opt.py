"""DSPy plumbing, offline, with DummyLM: metrics math, optimizers, save/load, second opinion.

These prove the pipeline, not prompt quality. Real gains need a real model:
``uv run --extra dspy ledger-hound dspy optimize``. Skipped when the extra isn't installed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

dspy = pytest.importorskip("dspy")

from dspy.utils import DummyLM  # noqa: E402

from ledger_hound.qa.brain import LLMSettings  # noqa: E402
from ledger_hound.qa.dspy_opt import (  # noqa: E402
    configure_lm,
    evaluate,
    load_program,
    optimize,
    predicted,
    second_opinion,
    to_examples,
)
from ledger_hound.qa.labels import split  # noqa: E402


def rows(n: int = 6) -> list[dict]:
    """Synthetic rows: first half violations; each attempt carries a unique marker."""
    return [
        {
            "rule": "invoice total <= received value",
            "attempt": f"probe case-{chr(65 + i)} attempted",
            "exchanges": f"POST /invoices -> {'201' if i < n // 2 else '422'}",
            "violation": i < n // 2,
        }
        for i in range(n)
    ]


def lm_for(data: list[dict], wrong: set[str] = frozenset()) -> DummyLM:
    """A fake model keyed on each case marker; markers in ``wrong`` get the wrong answer."""
    answers = {}
    for r in data:
        marker = r["attempt"].split()[1]
        truth = r["violation"] if marker not in wrong else not r["violation"]
        answers[marker] = {"reasoning": f"looked at {marker}", "violation": str(truth)}
    return DummyLM(answers)


def test_predicted_parses_bools_and_rejects_noise():
    assert predicted(SimpleNamespace(violation=True)) is True
    assert predicted(SimpleNamespace(violation=" false ")) is False
    assert predicted(SimpleNamespace(violation="maybe")) is None
    assert predicted(SimpleNamespace()) is None


def test_configure_lm_routes_through_openai_compatible_client():
    lm = configure_lm(LLMSettings("k", "http://router/v1", "openai/laguna-s-2.1"))
    assert lm.model == "openai/openai/laguna-s-2.1"  # router receives "openai/laguna-s-2.1"
    assert lm.kwargs["temperature"] == 0.0


def test_evaluate_confusion_matrix():
    data = rows()
    with dspy.context(lm=lm_for(data, wrong={"case-A", "case-D"})):
        from ledger_hound.qa.dspy_opt import ViolationClassifier

        s = evaluate(ViolationClassifier(), to_examples(data))
    assert (s.tp, s.fn, s.tn, s.fp) == (2, 1, 2, 1)
    assert s.accuracy == round(4 / 6, 4)
    assert s.precision == s.recall == round(2 / 3, 4)
    assert s.missed == ["probe case-A attempted"]


def test_labeled_optimize_saves_program_and_readable_prompt(tmp_path):
    data = rows(10)
    train, dev = data[:6], data[6:]
    with dspy.context(lm=lm_for(data)):
        report = optimize(train, dev, "labeled", tmp_path)
    assert report["optimized"]["accuracy"] == 1.0
    loaded = load_program(report["program"])
    [(_, predictor)] = loaded.named_predictors()
    assert len(predictor.demos) == 6
    assert "### Demos (6)" in (tmp_path / "prompt.md").read_text()


def test_bootstrap_keeps_models_correct_traces_as_demos(tmp_path):
    data = rows(10)
    with dspy.context(lm=lm_for(data)):
        report = optimize(data[:6], data[6:], "bootstrap", tmp_path)
    prompt = (tmp_path / "prompt.md").read_text()
    assert "reasoning: looked at case-" in prompt  # bootstrapped, not just labelled
    assert report["baseline"]["n"] == report["optimized"]["n"] == 4


def test_unknown_optimizer_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown optimizer"):
        optimize(rows()[:3], rows()[3:], "magic", tmp_path)


def test_second_opinion_compares_with_oracle():
    ex = [
        {
            "request": {"method": "POST", "path": "/x", "body": None},
            "response": {"status": 201, "body": {}},
        }
    ]
    results = [
        SimpleNamespace(
            attack="a1",
            rule="match_gate",
            title="case-A",
            expected="refuse",
            status="BREACHED",
            source="library",
            exchanges=ex,
            classifier={},
        ),
        SimpleNamespace(
            attack="a2",
            rule="gl_balance",
            title="case-B",
            expected="balance",
            status="HELD",
            source="library",
            exchanges=ex,
            classifier={},
        ),
        SimpleNamespace(
            attack="llm:x",
            rule="whatever",
            title="case-C",
            expected="?",
            status="BREACHED",
            source="llm",
            exchanges=ex,
            classifier={},
        ),
        SimpleNamespace(
            attack="a3",
            rule="gl_balance",
            title="case-D",
            expected="?",
            status="ERROR",
            source="library",
            exchanges=ex,
            classifier={},
        ),
    ]
    lm = DummyLM(
        {
            "case-A": {"reasoning": "posted", "violation": "True"},
            "case-B": {"reasoning": "looks off", "violation": "True"},  # disagrees with oracle
            "case-C": {"reasoning": "posted", "violation": "True"},
        }
    )
    from ledger_hound.qa.dspy_opt import ViolationClassifier
    from ledger_hound.qa.labels import render_exchanges

    with dspy.context(lm=lm):
        summary = second_opinion(ViolationClassifier(), results, render_exchanges)
    assert summary == {"agreement": "1/2", "disagreements": ["a2"]}
    assert results[0].classifier["agrees_with_oracle"] is True
    assert "agrees_with_oracle" not in results[2].classifier  # no oracle for LLM probes
    assert results[3].classifier == {}  # ERROR probes are not classified


def test_split_interleaves_labels_for_demo_selection():
    data = rows(20)
    train, _ = split(data, seed=7)
    assert len({r["violation"] for r in train[:4]}) == 2
