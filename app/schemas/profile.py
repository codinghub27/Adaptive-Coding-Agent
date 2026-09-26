"""Learner profile view schema.

The store assembles this view from the `LearnerProfile` row (converting the
`common_errors` frequency map into a most-frequent-first tag list); this
module only defines the shape and validates it.
"""

from pydantic import field_validator

from app.schemas.base import APIModel

__all__ = ["LearnerProfileView", "LearningPreferencesUpdate"]


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


class LearningPreferencesUpdate(APIModel):
    """Body of `PATCH /profile/preferences`.

    Every field is optional; only the ones supplied are merged into the
    stored preferences. The keys are declared explicitly rather than taken as
    a free-form mapping so a caller cannot write arbitrary keys into the
    profile's JSON column -- these are exactly the flags the teaching planner
    reads (`app.agents.planner.build_plan`).
    """

    prefers_hints: bool | None = None
    likes_step_by_step: bool | None = None
    wants_line_by_line_explanations: bool | None = None

    def as_mapping(self) -> dict[str, bool]:
        """The supplied flags only, ready to merge."""
        return {key: value for key, value in self.model_dump().items() if value is not None}
