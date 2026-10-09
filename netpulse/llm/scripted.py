"""Scripted generator: a deterministic stand-in for an LLM in tests and demos.

Each call consumes the next scripted step:

* ``str``: raw model text, parsed exactly like Ollama output (it may be malformed);
* ``dict`` or ``list``: JSON-like output (a list is wrapped as ``{"hypotheses": [...]}``);
* ``Exception`` instance: raised, as if the model call failed;
* ``callable(context)``: computes the output from the context (e.g. cite real evidence IDs).

When the script runs out, the last step repeats.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from netpulse.llm.base import GenerationContext, GenerationResult
from netpulse.llm.parsing import parse_output


class ScriptedGenerator:
    provider = "scripted"

    def __init__(self, steps: list[Any], model: str = "scripted-v1") -> None:
        if not steps:
            raise ValueError("at least one scripted step is required")
        self.steps = list(steps)
        self.model = model
        self.calls: list[GenerationContext] = []

    def generate(self, context: GenerationContext) -> GenerationResult:
        step = self.steps[min(len(self.calls), len(self.steps) - 1)]
        self.calls.append(context)
        if isinstance(step, Callable) and not isinstance(step, type):
            step = step(context)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, list):
            step = {"hypotheses": step}
        raw = step if isinstance(step, str) else json.dumps(step)
        return GenerationResult(
            hypotheses=parse_output(raw), provider=self.provider, model=self.model, raw_output=raw[:4000]
        )
