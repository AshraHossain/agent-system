"""Cost estimation from recorded token usage and a configurable price table.

No prices ship in code: they vary by model, tier, region and contract. The table
is a JSON file the operator supplies (see `prices.example.json`). Without a
table, or without an entry for the model, or without recorded tokens (the mock
model reports none), the estimate is `None` rather than a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

PER = 1_000_000


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_mtok: float = Field(ge=0, description="price per 1M prompt tokens")
    output_per_mtok: float = Field(ge=0, description="price per 1M output tokens")


class PriceTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(min_length=1, max_length=8)
    models: dict[str, ModelPrice]

    @field_validator("models")
    @classmethod
    def _not_empty(cls, v: dict[str, ModelPrice]) -> dict[str, ModelPrice]:
        if not v:
            raise ValueError("price table must price at least one model")
        return v

    def price_for(self, model: str) -> ModelPrice | None:
        return self.models.get(model)


class CostEstimate(BaseModel):
    currency: str
    input_tokens: int
    output_tokens: int
    total: float


def load_price_table(path: Path) -> PriceTable:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as e:
        raise ValueError(f"cannot read price table {path}: {e.strerror or e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"price table {path} is not valid JSON: {e}") from e
    try:
        return PriceTable.model_validate(raw)
    except ValueError as e:
        raise ValueError(f"invalid price table {path}: {e}") from e


def billable_tokens(
    prompt: int | None, completion: int | None, total: int | None
) -> tuple[int, int] | None:
    """(input, output) tokens to bill, or None when the provider reported no usage.

    Gemini's `total_token_count` also covers thinking tokens, which are billed as
    output but are not part of `candidates_token_count`. Anything in the total
    beyond prompt + completion is therefore charged at the output rate.
    """
    if prompt is None and completion is None:
        return None
    p, c = prompt or 0, completion or 0
    if total is not None and total > p + c:
        c = total - p
    return p, c


def estimate_cost(
    table: PriceTable | None,
    model: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None = None,
) -> CostEstimate | None:
    price = table.price_for(model) if table else None
    tokens = billable_tokens(prompt_tokens, completion_tokens, total_tokens)
    if table is None or price is None or tokens is None:
        return None
    tin, tout = tokens
    total = (tin * price.input_per_mtok + tout * price.output_per_mtok) / PER
    return CostEstimate(
        currency=table.currency, input_tokens=tin, output_tokens=tout, total=round(total, 6)
    )
