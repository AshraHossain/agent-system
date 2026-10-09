"""Runtime settings from environment variables (no secrets are required by default).

| Variable | Default | Meaning |
|---|---|---|
| ``NETPULSE_DATA_DIR`` | ``data/synthetic/v1`` | Synthetic dataset root |
| ``NETPULSE_LLM_PROVIDER`` | ``ollama`` | ``ollama`` or ``heuristic`` |
| ``NETPULSE_OLLAMA_URL`` | ``http://localhost:11434`` | Local Ollama endpoint |
| ``NETPULSE_OLLAMA_MODEL`` | ``qwen2.5:7b-instruct`` | Model tag |
| ``NETPULSE_LLM_FALLBACK`` | ``true`` | Fall back to the heuristic generator, *visibly*, if Ollama is unreachable |
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    llm_provider: Literal["ollama", "heuristic"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    llm_fallback: bool = True
    llm_temperature: float = Field(default=0.0, ge=0, le=1)
    llm_seed: int = 7
    llm_max_tokens: int = Field(default=1500, ge=200, le=8000)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_provider=os.environ.get("NETPULSE_LLM_PROVIDER", "ollama"),
            ollama_url=os.environ.get("NETPULSE_OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("NETPULSE_OLLAMA_MODEL", "qwen2.5:7b-instruct"),
            llm_fallback=_env_bool("NETPULSE_LLM_FALLBACK", True),
        )
