"""Investigation request and scope (output of the deterministic intake stage)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    def label(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"


class InvestigationScope(BaseModel):
    investigation_id: str
    dataset_id: str
    request_text: str = Field(max_length=4000, description="Validated, redacted request")
    reported_at: datetime
    window: TimeWindow
    baseline: TimeWindow
    candidate_services: list[str]
    named_services: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    search_query: str
    request_flags: list[str] = Field(
        default_factory=list, description="Security flags raised on the request itself"
    )
