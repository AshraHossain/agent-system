"""Pydantic contracts shared by the pipeline, store, tools and agent (no framework imports)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundingBox(BaseModel):
    model_config = ConfigDict(frozen=True)

    x_min: int = Field(ge=0)
    y_min: int = Field(ge=0)
    x_max: int = Field(gt=0)
    y_max: int = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> BoundingBox:
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("bounding box must have positive width and height")
        return self

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x_min, self.y_min, self.x_max, self.y_max)


class RawDetection(BaseModel):
    """What a detector returns: a box and its confidence."""

    box: BoundingBox
    confidence: float = Field(ge=0, le=1)


class PlateRecord(BaseModel):
    """One detected, read and tagged plate. This is what the vector store persists."""

    id: str = Field(description="Content-derived: sha256(image_sha256 + box)[:16]")
    image_sha256: str
    source: str = Field(description="Image file name relative to the inbox")
    box: BoundingBox
    raw_text: str
    plate_text: str = Field(description="Normalised: upper-case A-Z0-9 only")
    detection_confidence: float = Field(ge=0, le=1)
    ocr_confidence: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1, description="detection x ocr")
    tags: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    plate_id: str
    plate_text: str
    source: str
    score: float = Field(ge=-1, le=1, description="cosine similarity of the query and stored plate")
    exact: bool
    confidence: float
    tags: list[str]


class ScanSummary(BaseModel):
    images_seen: int
    images_new: int
    images_failed: int
    plates_stored: int
    failures: list[str] = Field(default_factory=list)


class PlateReport(BaseModel):
    """Structured answer returned by the PlateWatch agent."""

    summary: str = Field(max_length=800)
    scanned: ScanSummary | None = None
    matches: list[SearchHit] = Field(default_factory=list, max_length=20)
    needs_review: list[str] = Field(
        default_factory=list, description="plate_ids carrying the needs-review tag"
    )
