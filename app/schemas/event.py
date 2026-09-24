"""Learning event schemas.

`LearningEventCreate` is the input boundary for recording a learning event.
Free-text fields such as `problem` hold **untrusted user data** (the problem
statement text); they are stored and displayed, never treated as
instructions.
"""

from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, field_validator

from app.schemas.base import APIModel
from app.schemas.intent import Intent

__all__ = [
    "Difficulty",
    "LearningEventCreate",
    "LearningEventView",
    "RecordEventResult",
    "RequestedHelp",
    "slug_tag",
]

Difficulty = Literal["easy", "medium", "hard"]
RequestedHelp = Literal[
    "hint", "solution", "debug", "explanation", "review", "test_analysis", "approach"
]


def slug_tag(value: str) -> str:
    """Normalize a short tag: strip, lowercase, spaces -> underscores."""
    return value.strip().lower().replace(" ", "_")


class LearningEventCreate(APIModel):
    """A learning event submitted for recording.

    `event_id` is client-supplied (defaulted here) so resubmission of the
    same event is idempotent at the store layer.
    """

    event_id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID | None = None
    intent: Intent | None = None
    problem: str | None = None
    topic: str = Field(min_length=1, max_length=64)
    pattern: str | None = Field(default=None, max_length=64)
    difficulty: Difficulty | None = None
    requested_help: RequestedHelp | None = None
    hints_used: int = Field(default=0, ge=0, le=1000)
    needed_full_solution: bool = False
    errors: list[str] = Field(default_factory=list[str])
    solved: bool
    time_spent: int | None = Field(
        default=None, ge=0, le=100_000, description="Minutes spent, if known."
    )
    concepts: list[str] = Field(default_factory=list[str])

    @field_validator("problem", mode="after")
    @classmethod
    def _normalize_problem(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()[:200]
        return stripped or None

    @field_validator("topic", mode="before")
    @classmethod
    def _normalize_topic(cls, value: object) -> object:
        # An empty slug (e.g. from an all-whitespace topic) is left as `""`
        # here; `min_length=1` on the field then rejects it, same as any
        # other too-short topic.
        if isinstance(value, str):
            return slug_tag(value)
        return value

    @field_validator("pattern", mode="before")
    @classmethod
    def _normalize_pattern(cls, value: object) -> object:
        if isinstance(value, str):
            slug = slug_tag(value)
            return slug or None
        return value

    @field_validator("errors", mode="before")
    @classmethod
    def _normalize_errors(cls, value: object) -> object:
        # Only slug string items (dropping ones that go empty after
        # slugging); non-string items are left in place rather than
        # silently dropped, so Pydantic's `list[str]` type validation
        # rejects the payload instead of quietly discarding bad data.
        if isinstance(value, list):
            items = cast("list[object]", value)
            normalized: list[object] = []
            for item in items:
                if isinstance(item, str):
                    slug = slug_tag(item)
                    if slug:
                        normalized.append(slug)
                else:
                    normalized.append(item)
            return normalized
        return value

    @field_validator("concepts", mode="before")
    @classmethod
    def _normalize_concepts(cls, value: object) -> object:
        if isinstance(value, list):
            items = cast("list[object]", value)
            normalized: list[object] = []
            for item in items:
                if isinstance(item, str):
                    stripped = item.strip()
                    if stripped:
                        normalized.append(stripped)
                else:
                    normalized.append(item)
            return normalized
        return value


class LearningEventView(APIModel):
    """A learning event as stored, returned to callers."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    seq: int
    conversation_id: UUID | None
    intent: Intent | None
    problem: str | None
    topic: str
    pattern: str | None
    difficulty: Difficulty | None
    requested_help: RequestedHelp | None
    hints_used: int
    needed_full_solution: bool
    errors: list[str]
    solved: bool
    time_spent: int | None
    concepts: list[str]
    created_at: datetime


class RecordEventResult(APIModel):
    """Outcome of recording a learning event."""

    event: LearningEventView
    applied: bool
