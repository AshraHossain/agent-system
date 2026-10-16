"""Records that make up the judge report."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .http import Exchange

Verdict = Literal["as_expected", "unexpected"]
AttackStatus = Literal["HELD", "BREACHED", "ERROR", "SKIPPED"]


def _slim(ex: Exchange | None) -> dict[str, Any] | None:
    if ex is None:
        return None
    return {
        "request": {"method": ex.method, "path": ex.path, "body": ex.request_body},
        "response": {"status": ex.status, "body": ex.body},
    }


@dataclass
class Step:
    index: int
    name: str
    expected: str
    verdict: Verdict
    observed: str
    exchange: dict[str, Any] | None = None
    interpretation: str = ""

    @classmethod
    def build(
        cls,
        index: int,
        name: str,
        expected: str,
        ex: Exchange | None,
        failures: list[str],
        observed: str = "",
    ) -> Step:
        verdict: Verdict = "unexpected" if failures else "as_expected"
        if not observed:
            observed = f"HTTP {ex.status}" if ex else "no request"
        if failures:
            observed = f"{observed}; " + "; ".join(failures)
        return cls(index, name, expected, verdict, observed, _slim(ex))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttackResult:
    attack: str
    rule: str
    title: str
    status: AttackStatus
    expected: str
    observed: str
    evidence: dict[str, Any] = field(default_factory=dict)
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    severity: str = ""
    explanation: str = ""
    source: str = "library"  # "library" (deterministic) or "llm" (improvised, unverified)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slim_all(exchanges: list[Exchange]) -> list[dict[str, Any]]:
    return [s for s in (_slim(e) for e in exchanges) if s]
