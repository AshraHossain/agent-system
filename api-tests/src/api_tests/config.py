"""Suite settings, read from the environment once per session."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = PROJECT_ROOT.parent / "traffic-flow-analyzer"


@dataclass(frozen=True)
class Settings:
    base_url: str | None  # None => start the target locally
    token: str | None
    timeout_s: float
    target_project: Path
    startup_timeout_s: float
    perf_samples: int
    perf_p95_ms: float

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            base_url=(env("API_BASE_URL") or "").rstrip("/") or None,
            token=env("API_TOKEN") or None,
            timeout_s=float(env("API_TIMEOUT", "10")),
            target_project=Path(env("API_TARGET_PROJECT", str(DEFAULT_TARGET))),
            startup_timeout_s=float(env("API_STARTUP_TIMEOUT", "90")),
            perf_samples=int(env("API_PERF_SAMPLES", "20")),
            perf_p95_ms=float(env("API_PERF_P95_MS", "500")),
        )
