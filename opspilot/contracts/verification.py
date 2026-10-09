"""Deterministic verification output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "error"]


class Check(BaseModel):
    name: str
    passed: bool
    details: str = ""


class VerificationIssue(BaseModel):
    severity: Severity
    code: str
    message: str
    refs: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    checks: list[Check]
    issues: list[VerificationIssue]
    invalid_citations: list[str]
    unsupported_claims: list[str]
    contradictions: list[str]
    policy_violations: list[str]
    missing_information: list[str]
    additional_evidence_requests: list[str]
    claims_checked: int
    verdict: Literal["pass", "needs_review", "fail"]
