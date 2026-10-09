"""Ollama-backed hypothesis generator (default provider; local, no paid service).

Uses ``langchain_ollama.ChatOllama`` with the output JSON schema passed as
Ollama's ``format`` (constrained decoding), temperature 0 and a fixed seed.
The raw text is parsed strictly by ``parse_output``. The ``invoke`` seam
lets tests substitute a fake model without a running Ollama server.
"""

from __future__ import annotations

from collections.abc import Callable

from netpulse.config import Settings
from netpulse.llm.base import GenerationContext, GenerationError, GenerationResult
from netpulse.llm.parsing import output_json_schema, parse_output
from netpulse.llm.prompts import build_messages

Invoke = Callable[[list[tuple[str, str]]], str]


class OllamaUnavailableError(GenerationError):
    """The Ollama server could not be reached (connection refused, DNS, etc.)."""


class OllamaGenerator:
    provider = "ollama"

    def __init__(self, settings: Settings, timeout_seconds: float = 120.0, invoke: Invoke | None = None) -> None:
        self.model = settings.ollama_model
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._invoke = invoke or self._ollama_invoke

    def _ollama_invoke(self, messages: list[tuple[str, str]]) -> str:
        from langchain_ollama import ChatOllama  # imported lazily: optional at test time

        llm = ChatOllama(
            model=self.settings.ollama_model,
            base_url=self.settings.ollama_url,
            temperature=self.settings.llm_temperature,
            seed=self.settings.llm_seed,
            num_predict=self.settings.llm_max_tokens,
            format=output_json_schema(),
            client_kwargs={"timeout": self.timeout_seconds},
        )
        try:
            return str(llm.invoke(messages).content)
        except (ConnectionError, OSError) as exc:
            raise OllamaUnavailableError(f"Ollama unreachable at {self.settings.ollama_url}: {exc}") from exc
        except Exception as exc:
            if type(exc).__name__ in {"ConnectError", "ConnectTimeout", "ResponseError"}:
                raise OllamaUnavailableError(f"Ollama error: {exc}") from exc
            raise

    def generate(self, context: GenerationContext) -> GenerationResult:
        raw = self._invoke(build_messages(context))
        return GenerationResult(
            hypotheses=parse_output(raw), provider=self.provider, model=self.model, raw_output=raw[:4000]
        )
