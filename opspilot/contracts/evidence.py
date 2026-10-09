"""Evidence records: the unit of traceability across the investigation."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

EVIDENCE_ID_PATTERN = r"^EV-(TEL|TOP|DOC|INC|DQ)-[0-9a-f]{8}$"
EVIDENCE_ID_RE = re.compile(EVIDENCE_ID_PATTERN)
STATE_PREFIX = "evidence:"

Scalar = str | int | float | bool | None


class EvidenceKind(StrEnum):
    TELEMETRY = "TEL"
    TOPOLOGY = "TOP"
    DOCUMENT = "DOC"
    INCIDENT = "INC"
    DATA_QUALITY = "DQ"


class Evidence(BaseModel):
    """A small, citable fact produced by a deterministic tool.

    IDs are derived from the canonical content, so the same fact always gets the
    same ID (idempotent across retries and parallel branches).
    """

    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    kind: EvidenceKind
    summary: str = Field(max_length=400)
    source: str = Field(max_length=200, description="Where the fact came from")
    entity_id: str | None = None
    metric: str | None = None
    data: dict[str, Scalar] = Field(default_factory=dict)
    trusted: bool = Field(
        default=True, description="False for retrieved documents (untrusted content)"
    )

    @field_validator("data")
    @classmethod
    def _small(cls, v: dict[str, Scalar]) -> dict[str, Scalar]:
        if len(v) > 16:
            raise ValueError("evidence data must stay small (<=16 fields)")
        return v

    @classmethod
    def make(
        cls,
        kind: EvidenceKind,
        summary: str,
        source: str,
        *,
        entity_id: str | None = None,
        metric: str | None = None,
        data: dict[str, Any] | None = None,
        trusted: bool = True,
    ) -> Evidence:
        data = {k: _round(v) for k, v in (data or {}).items()}
        canonical = json.dumps(
            {"k": kind.value, "s": source, "e": entity_id, "m": metric, "d": data},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        return cls(
            evidence_id=f"EV-{kind.value}-{digest}",
            kind=kind,
            summary=summary[:400],
            source=source[:200],
            entity_id=entity_id,
            metric=metric,
            data=data,
            trusted=trusted,
        )

    def state_key(self) -> str:
        return f"{STATE_PREFIX}{self.evidence_id}"


def _round(v: Any) -> Scalar:
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (str, int, bool)) or v is None:
        return v
    return str(v)


def is_evidence_id(value: str) -> bool:
    return bool(EVIDENCE_ID_RE.match(value))
