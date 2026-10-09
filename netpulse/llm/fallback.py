"""Visible fallback: if the primary provider is unreachable, use the backup and say so."""

from __future__ import annotations

from netpulse.llm.base import GenerationContext, GenerationResult, HypothesisGenerator
from netpulse.llm.ollama import OllamaUnavailableError


class FallbackGenerator:
    def __init__(self, primary: HypothesisGenerator, backup: HypothesisGenerator) -> None:
        self.primary, self.backup = primary, backup
        self.provider = f"{primary.provider}->fallback:{backup.provider}"
        self.model = primary.model

    def generate(self, context: GenerationContext) -> GenerationResult:
        try:
            return self.primary.generate(context)
        except OllamaUnavailableError as exc:
            result = self.backup.generate(context)
            note = f"FALLBACK: {self.primary.provider} unavailable ({str(exc)[:200]}); used {self.backup.provider}"
            return result.model_copy(update={"provider": f"{result.provider} (fallback)", "raw_output": note})
