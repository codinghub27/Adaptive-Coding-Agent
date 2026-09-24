"""Learner profile view schema.

The store assembles this view from the `LearnerProfile` row (converting the
`common_errors` frequency map into a most-frequent-first tag list); this
module only defines the shape and validates it.
"""

from pydantic import field_validator

from app.schemas.base import APIModel

__all__ = ["LearnerProfileView"]


class LearnerProfileView(APIModel):
    """A learner's aggregated skill levels, preferences, and error patterns."""

    language: str | None = None
    skill_levels: dict[str, float]
    learning_preferences: dict[str, bool]
    common_errors: list[str]

    @field_validator("skill_levels")
    @classmethod
    def _validate_skill_range(cls, value: dict[str, float]) -> dict[str, float]:
        for skill, level in value.items():
            if not 0.0 <= level <= 1.0:
                raise ValueError(f"skill level for {skill!r} must be within 0.0-1.0")
        return value

    @classmethod
    def empty(cls) -> "LearnerProfileView":
        """A neutral profile view for an unknown/anonymous learner."""
        return cls(language=None, skill_levels={}, learning_preferences={}, common_errors=[])
