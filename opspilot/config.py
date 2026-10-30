"""Runtime configuration. Everything is overridable via environment variables.

No secrets live here: Gemini credentials are read by google-genai from the
environment (GOOGLE_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI + GOOGLE_CLOUD_PROJECT
+ GOOGLE_CLOUD_LOCATION with Application Default Credentials).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from opspilot.core.auth import hash_token, parse_digests
from opspilot.core.cost import PriceTable, load_price_table

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Limits:
    max_llm_calls: int = 40
    max_llm_calls_per_agent: int = 8
    max_tool_calls: int = 60
    max_duration_s: float = 120.0
    max_evidence_records: int = 200
    max_model_attempts: int = 2
    retry_initial_delay_s: float = 1.0
    retry_max_delay_s: float = 8.0
    model_timeout_s: float = 60.0
    max_request_chars: int = 4000
    max_report_chars: int = 60_000
    # 1 = single pass (default). 2 = one extra round that retries failed stages.
    max_investigation_rounds: int = 1

    def __post_init__(self) -> None:
        if self.max_investigation_rounds not in (1, 2):
            raise ValueError(
                f"max_investigation_rounds must be 1 or 2, got {self.max_investigation_rounds!r}"
            )

    @classmethod
    def from_env(cls) -> Limits:
        return cls(
            max_llm_calls=_int("OPSPILOT_MAX_LLM_CALLS", cls.max_llm_calls),
            max_llm_calls_per_agent=_int(
                "OPSPILOT_MAX_LLM_CALLS_PER_AGENT", cls.max_llm_calls_per_agent
            ),
            max_tool_calls=_int("OPSPILOT_MAX_TOOL_CALLS", cls.max_tool_calls),
            max_duration_s=_float("OPSPILOT_MAX_DURATION_S", cls.max_duration_s),
            max_evidence_records=_int("OPSPILOT_MAX_EVIDENCE", cls.max_evidence_records),
            max_model_attempts=_int("OPSPILOT_MAX_MODEL_ATTEMPTS", cls.max_model_attempts),
            retry_initial_delay_s=_float(
                "OPSPILOT_RETRY_INITIAL_DELAY_S", cls.retry_initial_delay_s
            ),
            retry_max_delay_s=_float("OPSPILOT_RETRY_MAX_DELAY_S", cls.retry_max_delay_s),
            model_timeout_s=_float("OPSPILOT_MODEL_TIMEOUT_S", cls.model_timeout_s),
            max_request_chars=_int("OPSPILOT_MAX_REQUEST_CHARS", cls.max_request_chars),
            max_report_chars=_int("OPSPILOT_MAX_REPORT_CHARS", cls.max_report_chars),
            max_investigation_rounds=_int(
                "OPSPILOT_MAX_INVESTIGATION_ROUNDS", cls.max_investigation_rounds
            ),
        )


@dataclass(frozen=True)
class EmbeddingConfig:
    """Vector side of knowledge retrieval. `hashing` is the offline default; `gemini`
    uses Gemini embeddings behind a persistent cache (ADR-0011)."""

    provider: Literal["hashing", "gemini"] = "hashing"
    model: str = "gemini-embedding-001"
    dims: int = 768
    cache_path: Path = REPO_ROOT / "var" / "embeddings.db"
    # None = the provider's default cutoff. Cosine scales differ per embedder.
    min_cosine: float | None = None

    def __post_init__(self) -> None:
        if self.provider not in ("hashing", "gemini"):
            raise ValueError(f"embedder must be 'hashing' or 'gemini', got {self.provider!r}")
        if not 1 <= self.dims <= 3072:
            raise ValueError(f"embedding dims must be between 1 and 3072, got {self.dims!r}")
        if self.min_cosine is not None and not -1.0 <= self.min_cosine <= 1.0:
            raise ValueError(f"embedding min_cosine must be in [-1, 1], got {self.min_cosine!r}")

    @property
    def label(self) -> str:
        return "hashing" if self.provider == "hashing" else f"{self.model}@{self.dims}"

    @classmethod
    def from_env(cls) -> EmbeddingConfig:
        raw_cosine = os.getenv("OPSPILOT_EMBEDDING_MIN_COSINE")
        return cls(
            provider=os.getenv("OPSPILOT_EMBEDDER", "hashing").lower(),  # type: ignore[arg-type]
            model=os.getenv("OPSPILOT_EMBEDDING_MODEL", cls.model),
            dims=_int("OPSPILOT_EMBEDDING_DIMS", cls.dims),
            cache_path=Path(os.getenv("OPSPILOT_EMBEDDING_CACHE", str(cls.cache_path))),
            min_cosine=float(raw_cosine) if raw_cosine else None,
        )


@dataclass(frozen=True)
class Settings:
    provider: Literal["mock", "gemini"] = "mock"
    model: str = "gemini-2.5-flash"
    data_dir: Path = REPO_ROOT / "var" / "datasets"
    session_db_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'var' / 'sessions.db'}"
    default_dataset: str = "C02"
    limits: Limits = field(default_factory=Limits)
    prices: PriceTable | None = None
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    # Bearer tokens for `opspilot serve` (ADR-0013): a token itself (OPSPILOT_API_TOKEN) and/or
    # SHA-256 digests of tokens (OPSPILOT_API_TOKEN_SHA256). Neither = no authentication.
    api_token: str | None = field(default=None, repr=False)
    api_token_digests: tuple[str, ...] = ()

    @property
    def token_digests(self) -> tuple[str, ...]:
        """Every accepted token, as digests: the configured digests plus the plain token's."""
        plain = (hash_token(self.api_token),) if self.api_token else ()
        return tuple(dict.fromkeys(self.api_token_digests + plain))

    @classmethod
    def from_env(cls) -> Settings:
        provider = os.getenv("OPSPILOT_MODEL_PROVIDER", "mock").lower()
        if provider not in ("mock", "gemini"):
            raise ValueError(
                f"OPSPILOT_MODEL_PROVIDER must be 'mock' or 'gemini', got {provider!r}"
            )
        return cls(
            provider=provider,  # type: ignore[arg-type]
            model=os.getenv("OPSPILOT_MODEL", cls.model),
            data_dir=Path(os.getenv("OPSPILOT_DATA_DIR", str(cls.data_dir))),
            session_db_url=os.getenv("OPSPILOT_SESSION_DB", cls.session_db_url),
            default_dataset=os.getenv("OPSPILOT_DEFAULT_DATASET", cls.default_dataset),
            limits=Limits.from_env(),
            embedding=EmbeddingConfig.from_env(),
            api_token_digests=parse_digests(os.getenv("OPSPILOT_API_TOKEN_SHA256")),
            prices=load_price_table(Path(table))
            if (table := os.getenv("OPSPILOT_PRICE_TABLE"))
            else None,
            api_token=_api_token(),
        )


MIN_TOKEN_LENGTH = 32


def _api_token() -> str | None:
    token = os.getenv("OPSPILOT_API_TOKEN", "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"OPSPILOT_API_TOKEN must be at least {MIN_TOKEN_LENGTH} characters; "
            "create one with `opspilot token`"
        )
    return token
