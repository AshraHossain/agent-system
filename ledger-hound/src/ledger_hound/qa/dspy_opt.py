"""DSPy: optimize the prompt that classifies "is this a financial-integrity violation?".

Where this fits. The Decimal oracle judges every *library* probe exactly. But
some things have no hand-written oracle: probes the LLM improvises, APIs whose
response shapes we have never seen, a production monitor reading live
traffic. For those, a model has to read a transcript and decide. This module
makes that judgement as good as it can be, *measurably*:

1. a DSPy ``Signature`` declares the task (rule + attempt + HTTP transcript ->
   violation: bool), with no hand-tuned prompt text;
2. the labelled dataset from ``labels.py`` (oracle-verified, built from the
   planted bugs) is split into train and dev;
3. an optimizer improves the prompt on train:
   ``labeled`` (k labelled demos, no LM calls to compile),
   ``bootstrap`` (BootstrapFewShot: keeps the model's own correct reasoning traces as demos) or
   ``mipro`` (MIPROv2: also rewrites the instructions; many more calls);
4. baseline and optimized programs are scored on the held-out dev set:
   accuracy, precision, recall and the confusion matrix.

Recall matters most here. A missed violation (false negative) is a bug that
ships; a false alarm costs a human five minutes.

DSPy is an optional extra: ``uv sync --extra dspy``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import dspy

from .brain import LLMSettings

INPUTS = ("rule", "attempt", "exchanges")


class ViolationSignature(dspy.Signature):
    """You audit a Purchase-to-Pay (accounts payable) API for financial-integrity bugs.
    Given a financial-control rule, what a probe attempted, and the raw HTTP transcript,
    decide whether the API's actual behaviour violated the rule. Judge from what the API
    accepted, refused, flagged or posted, and from the amounts in its responses."""

    rule: str = dspy.InputField(desc="the financial control being tested")
    attempt: str = dspy.InputField(desc="what the probe tried, and what a correct system does")
    exchanges: str = dspy.InputField(desc="HTTP transcript, oldest call first")
    violation: bool = dspy.OutputField(desc="True if the API breached the rule, else False")


class ViolationClassifier(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.judge = dspy.ChainOfThought(ViolationSignature)

    def forward(self, rule: str, attempt: str, exchanges: str) -> dspy.Prediction:
        return self.judge(rule=rule, attempt=attempt, exchanges=exchanges)


def configure_lm(settings: LLMSettings) -> dspy.LM:
    """DSPy LM for an OpenAI-compatible router.

    The ``openai/`` prefix selects the OpenAI-compatible client; everything after
    it is sent as the model name. So ``openai/laguna-s-2.1`` on the router is
    addressed as ``openai/openai/laguna-s-2.1`` here.
    """
    return dspy.LM(
        f"openai/{settings.model}",
        api_base=settings.base_url,
        api_key=settings.api_key,
        temperature=0.0,
        max_tokens=1200,
    )


def to_examples(rows: list[dict[str, Any]]) -> list[dspy.Example]:
    return [
        dspy.Example(**{k: r[k] for k in INPUTS}, violation=r["violation"]).with_inputs(*INPUTS)
        for r in rows
    ]


def predicted(pred: Any) -> bool | None:
    value = getattr(pred, "violation", None)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None  # unparseable answers count as wrong


def metric(example: dspy.Example, pred: Any, trace: Any = None) -> bool:
    return predicted(pred) is example.violation


@dataclass
class Scores:
    n: int
    accuracy: float
    precision: float
    recall: float
    tp: int
    fp: int
    tn: int
    fn: int
    unparsed: int
    missed: list[str]  # attempts the program called clean but were violations

    def line(self) -> str:
        return (
            f"accuracy {self.accuracy:.0%}  precision {self.precision:.0%}  "
            f"recall {self.recall:.0%}  (tp {self.tp} fp {self.fp} tn {self.tn} fn {self.fn})"
        )


def evaluate(program: dspy.Module, devset: list[dspy.Example], threads: int = 1) -> Scores:
    result = dspy.Evaluate(devset=devset, metric=metric, num_threads=threads)(program)
    tp = fp = tn = fn = unparsed = 0
    missed: list[str] = []
    for example, pred, _ in result.results:
        guess = predicted(pred)
        if guess is None:
            unparsed += 1
            fn += example.violation
            fp += not example.violation
            if example.violation:
                missed.append(example.attempt[:120])
        elif guess and example.violation:
            tp += 1
        elif guess:
            fp += 1
        elif example.violation:
            fn += 1
            missed.append(example.attempt[:120])
        else:
            tn += 1
    n = len(devset)
    return Scores(
        n=n,
        accuracy=round((tp + tn) / n, 4) if n else 0.0,
        precision=round(tp / (tp + fp), 4) if tp + fp else 0.0,
        recall=round(tp / (tp + fn), 4) if tp + fn else 0.0,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        unparsed=unparsed,
        missed=missed,
    )


def compile_program(
    optimizer: str, trainset: list[dspy.Example], threads: int = 1
) -> ViolationClassifier:
    student = ViolationClassifier()
    if optimizer == "labeled":
        return dspy.LabeledFewShot(k=8).compile(student, trainset=trainset)
    if optimizer == "bootstrap":
        tp = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=4)
        return tp.compile(student, trainset=trainset)
    if optimizer == "mipro":
        tp = dspy.MIPROv2(metric=metric, auto="light", num_threads=threads, seed=7)
        return tp.compile(student, trainset=trainset, requires_permission_to_run=False)
    raise ValueError(f"unknown optimizer {optimizer!r} (labeled | bootstrap | mipro)")


def optimize(
    train_rows: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    optimizer: str,
    out_dir: Path,
    threads: int = 1,
) -> dict[str, Any]:
    """Score the baseline, compile, score again, save the program and its prompt.

    Uses whatever LM is active in ``dspy.settings`` (configure or ``dspy.context``).
    """
    trainset, devset = to_examples(train_rows), to_examples(dev_rows)
    baseline = evaluate(ViolationClassifier(), devset, threads)
    compiled = compile_program(optimizer, trainset, threads)
    tuned = evaluate(compiled, devset, threads)

    out_dir.mkdir(parents=True, exist_ok=True)
    program_path = out_dir / "classifier.json"
    prompt_path = out_dir / "prompt.md"
    compiled.save(str(program_path))
    prompt_path.write_text(describe_prompt(compiled))
    return {
        "optimizer": optimizer,
        "train": len(trainset),
        "dev": len(devset),
        "baseline": asdict(baseline),
        "optimized": asdict(tuned),
        "accuracy_delta": round(tuned.accuracy - baseline.accuracy, 4),
        "recall_delta": round(tuned.recall - baseline.recall, 4),
        "program": str(program_path),
        "prompt": str(prompt_path),
    }


def load_program(path: str | Path) -> ViolationClassifier:
    program = ViolationClassifier()
    program.load(str(path))
    return program


def describe_prompt(program: dspy.Module) -> str:
    """Human-readable dump of what the optimizer produced: instructions + demos."""
    parts = ["# Optimized violation classifier\n"]
    for name, predictor in program.named_predictors():
        parts.append(f"## Predictor `{name}`\n")
        parts.append("### Instructions\n")
        parts.append(predictor.signature.instructions.strip() + "\n")
        parts.append(f"### Demos ({len(predictor.demos)})\n")
        for i, demo in enumerate(predictor.demos, 1):
            d = demo.toDict() if hasattr(demo, "toDict") else dict(demo)
            reasoning = d.get("reasoning")
            parts.append(f"**{i}. violation = {d.get('violation')}**  ")
            parts.append(f"attempt: {d.get('attempt', '')}  ")
            if reasoning:
                parts.append(f"reasoning: {str(reasoning)[:400]}  ")
            parts.append("")
    return "\n".join(parts)


def second_opinion(program: dspy.Module, results: list[Any], render: Any) -> dict[str, Any]:
    """Run the classifier over attack results and compare with the oracle.

    ``results`` are ``AttackResult``s. Library probes get an agreement check;
    LLM-improvised probes (no oracle) get the classifier's verdict alone, which
    is the case this classifier exists for.
    """
    from .labels import RULE_TEXT

    agree = total = 0
    disagreements: list[str] = []
    for r in results:
        if r.status not in {"HELD", "BREACHED"}:
            continue
        try:
            pred = program(
                rule=RULE_TEXT.get(r.rule, RULE_TEXT["any"]),
                attempt=f"{r.title}. A correct system: {r.expected}.",
                exchanges=render(r.exchanges),
            )
            guess = predicted(pred)
            reasoning = str(getattr(pred, "reasoning", ""))[:300]
        except Exception as exc:  # noqa: BLE001 - a classifier failure must not sink the report
            guess, reasoning = None, f"classifier error: {type(exc).__name__}: {exc}"[:300]
        entry: dict[str, Any] = {"violation": guess, "reasoning": reasoning}
        if r.source == "library":
            ok = guess is (r.status == "BREACHED")
            entry["agrees_with_oracle"] = ok
            total += 1
            agree += ok
            if not ok:
                disagreements.append(r.attack)
        r.classifier = entry
    return {"agreement": f"{agree}/{total}", "disagreements": disagreements}
