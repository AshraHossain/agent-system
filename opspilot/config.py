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
    model_timeout_s: float = 60.0
    max_request_chars: int = 4000
    max_report_chars: int = 60_000

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
            model_timeout_s=_float("OPSPILOT_MODEL_TIMEOUT_S", cls.model_timeout_s),
            max_request_chars=_int("OPSPILOT_MAX_REQUEST_CHARS", cls.max_request_chars),
            max_report_chars=_int("OPSPILOT_MAX_REPORT_CHARS", cls.max_report_chars),
        )


@dataclass(frozen=True)
class Settings:
    provider: Literal["mock", "gemini"] = "mock"
    model: str = "gemini-2.5-flash"
    data_dir: Path = REPO_ROOT / "var" / "datasets"
    session_db_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'var' / 'sessions.db'}"
    default_dataset: str = "C02"
    limits: Limits = field(default_factory=Limits)

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
        )
