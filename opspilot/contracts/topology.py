"""Topology tool contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from opspilot.contracts.evidence import Evidence

Impact = Literal["critical", "high", "medium", "low"]
Exposure = Literal["single_point", "redundant", "indirect"]


class ComponentInfo(BaseModel):
    component_id: str
    type: str
    redundancy_group: str | None = None
    interfaces: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    tier: int | None = None
    known: bool = True
    evidence: list[Evidence] = Field(default_factory=list)


class DependencyResult(BaseModel):
    services: list[str]
    dependencies: dict[str, list[str]] = Field(
        description="service -> network components in its dependency closure"
    )
    shared_components: dict[str, list[str]] = Field(
        description="component -> services (>=2) that depend on it"
    )
    unknown_services: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class ServiceImpact(BaseModel):
    service: str
    tier: int
    exposure: Exposure
    impact: Impact


class BlastRadius(BaseModel):
    component_id: str
    impacts: list[ServiceImpact]
    rules: list[str]
    uncertainties: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
