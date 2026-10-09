"""Knowledge retrieval contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opspilot.contracts.evidence import Evidence

DocFlag = Literal["deprecated", "superseded", "suspected_injection", "redacted", "no_components"]


class DocumentHit(BaseModel):
    doc_id: str
    doc_type: Literal["runbook", "tech_doc", "incident_report"]
    title: str
    status: str
    updated: str
    score: float = Field(description="RRF fused score (rank-based, not a probability)")
    matched_by: list[Literal["keyword", "vector"]]
    snippet: str = Field(description="Sanitised excerpt wrapped as untrusted content")
    flags: list[DocFlag] = Field(default_factory=list)
    superseded_by: str | None = None
    root_cause_category: str | None = None
    components: list[str] = Field(default_factory=list)
    evidence_id: str


class SearchResult(BaseModel):
    query: str
    doc_type: str
    hits: list[DocumentHit]
    flagged: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
