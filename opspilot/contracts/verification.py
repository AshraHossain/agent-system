"""Deterministic verification output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "error"]


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    details: str = ""


class VerificationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: Severity
    code: str
    message: str
    refs: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    checks: list[Check]
    issues: list[VerificationIssue]
    invalid_citations: list[str]
    unsupported_claims: list[str]
    contradictions: list[str]
    policy_violations: list[str]
    missing_information: list[str]
    additional_evidence_requests: list[str]
    claims_checked: int
    irrelevant_citations: list[str] = Field(default_factory=list)
    citations_relevance_checked: int = 0
    verdict: Literal["pass", "needs_review", "fail"]
