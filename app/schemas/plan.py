"""Teaching plan schema.

A `TeachingPlan` is **data, not prose**: it is a structured set of decisions
(difficulty, assistance level, strategy, and tunable knobs) produced by the
teaching planner for a specific turn. Downstream agents and the response
generator consume these fields directly; nothing here is a natural-language
instruction to an LLM.
"""

from typing import Final, Literal

from pydantic import Field

from app.schemas.base import APIModel
from app.schemas.event import Difficulty

__all__ = [
    "ASSISTANCE_ORDER",
    "AssistanceLevel",
    "SolutionStrategy",
    "TeachingPlan",
]

AssistanceLevel = Literal["hint", "concept", "pseudocode", "partial", "full"]

ASSISTANCE_ORDER: Final[tuple[AssistanceLevel, ...]] = (
    "hint",
    "concept",
    "pseudocode",
    "partial",
    "full",
)
"""`AssistanceLevel` values ordered from least to most help given."""

SolutionStrategy = Literal[
    "socratic_hints",
    "guided_debugging",
    "step_by_step_explanation",
    "concise_review",
    "clarify",
]


class TeachingPlan(APIModel):
    """The teaching planner's decision for how to help on this turn."""

    difficulty: Difficulty
    assistance_level: AssistanceLevel
    solution_strategy: SolutionStrategy
    topic: str | None = Field(default=None, max_length=64)
    skill_level: float = Field(ge=0.0, le=1.0)
    step_by_step: bool = False
    concise: bool = False
    watch_errors: list[str] = Field(default_factory=list[str], max_length=5)
    rationale: list[str] = Field(
        default_factory=list[str],
        description=(
            "Short machine-readable rule codes explaining the decision, e.g. "
            '"weak_skill", "prefers_hints" -- not free text for display.'
        ),
    )
