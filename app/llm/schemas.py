from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AssignmentIntent(BaseModel):
    intent: Literal["create_assignment", "clarify"]
    title: str = Field(min_length=3, max_length=240)
    instructions: str = Field(min_length=3, max_length=5000)
    due_at: datetime
    confidence: float = Field(ge=0, le=1)
    clarification_question: str | None = Field(default=None, max_length=500)

    @field_validator("due_at")
    @classmethod
    def due_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        return value


class ProgressIntent(BaseModel):
    status: Literal["acknowledged", "in_progress", "blocked"]
    message: str = Field(min_length=1, max_length=2000)
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(ge=0, le=1)


class RiskSummary(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
