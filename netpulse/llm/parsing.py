"""Strict parsing of model output into ``Hypothesis`` objects.

The model fills a reduced schema (``LLMHypothesis``). It has no IDs, no
provenance, and no numeric confidence. IDs and ``generated_by`` are
assigned here. Anything that does not validate raises ``GenerationError``,
which counts as a failed attempt. Nothing is repaired by guessing.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from netpulse.llm.base import GenerationError
from netpulse.models import Hypothesis, RootCauseCategory

MAX_HYPOTHESES = 5


class LLMHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=10, max_length=800)
    cause_category: RootCauseCategory
    suspected_root_entity: str | None = None
    affected_components: list[str] = Field(default_factory=list, max_length=20)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=30)
    contradicting_evidence: list[str] = Field(default_factory=list, max_length=30)
    alternative_explanations: list[str] = Field(default_factory=list, max_length=5)
    missing_evidence: list[str] = Field(default_factory=list, max_length=10)
    validation_steps: list[str] = Field(default_factory=list, max_length=6)
    confidence_rationale: str = Field(min_length=1, max_length=800)


class LLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[LLMHypothesis] = Field(max_length=MAX_HYPOTHESES)


def output_json_schema() -> dict:
    return LLMOutput.model_json_schema()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_output(raw: str | dict) -> list[Hypothesis]:
    try:
        data = json.loads(_FENCE.sub("", raw.strip())) if isinstance(raw, str) else raw
        parsed = LLMOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError, AttributeError) as exc:
        raise GenerationError(f"model output failed schema validation: {str(exc)[:400]}") from exc
    return [
        Hypothesis(hypothesis_id=f"hyp-{n:02d}", generated_by="llm", **h.model_dump())
        for n, h in enumerate(parsed.hypotheses, start=1)
    ]
