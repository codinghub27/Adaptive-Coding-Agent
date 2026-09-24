"""Deterministic teaching planner: intent + learner profile -> `TeachingPlan`.

Pure and LLM-free by design (Phase 4 scope): every decision here is a fixed
rule applied to the classified intent, the learner profile, and a topic/skill
analysis of the current turn, so it is testable and auditable without a
model in the loop. The planner only ever proposes assistance up to
`MAX_INITIAL_ASSISTANCE` on a first turn -- "full" is reached later, by the
Phase 7 hint ladder as a learner works through progressively stronger hints.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

# `PRIOR` (the neutral starting skill for an unseen topic) is owned by the
# profile store; the planner reuses it rather than redefining its own.
from app.memory.profile import PRIOR
from app.schemas.base import APIModel
from app.schemas.event import Difficulty, slug_tag
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, SolutionStrategy, TeachingPlan
from app.schemas.profile import LearnerProfileView

__all__ = [
    "HARD_SKILL",
    "INTENT_DEFAULTS",
    "MAX_INITIAL_ASSISTANCE",
    "STRONG_SKILL",
    "WEAK_SKILL",
    "ProblemAnalysis",
    "analyze_problem",
    "build_plan",
    "difficulty_for",
]

WEAK_SKILL: Final = 0.4
STRONG_SKILL: Final = 0.75
HARD_SKILL: Final = 0.7
MAX_INITIAL_ASSISTANCE: Final[AssistanceLevel] = "partial"

INTENT_DEFAULTS: Final[Mapping[Intent, tuple[AssistanceLevel, SolutionStrategy]]] = (
    MappingProxyType(
        {
            Intent.DSA_HINT: ("hint", "socratic_hints"),
            Intent.DSA_SOLVE: ("concept", "socratic_hints"),
            Intent.APPROACH_DISCUSSION: ("concept", "socratic_hints"),
            Intent.CODE_DEBUG: ("hint", "guided_debugging"),
            Intent.ERROR_EXPLANATION: ("concept", "guided_debugging"),
            Intent.TEST_CASE_ANALYSIS: ("hint", "guided_debugging"),
            Intent.CODE_EXPLAIN: ("concept", "step_by_step_explanation"),
            Intent.CONCEPT_EXPLANATION: ("concept", "step_by_step_explanation"),
            Intent.IMAGE_CODE_ANALYSIS: ("concept", "step_by_step_explanation"),
            Intent.CODE_REVIEW: ("concept", "concise_review"),
            Intent.OPTIMIZATION: ("concept", "concise_review"),
        }
    )
)


class ProblemAnalysis(APIModel):
    """A topic/skill inference for the current turn, independent of intent."""

    topic: str | None
    skill_level: float
    topic_source: Literal["hint", "profile_match", "unknown"]


def _prose(inp: StructuredInput | None) -> str:
    """The learner-authored prose to match topics against: never code.

    Code identifiers (variable/function names) frequently collide with skill
    keys by coincidence, so only the question, problem statement, and error
    text are considered.
    """
    if inp is None:
        return ""
    parts = [part for part in (inp.question, inp.problem, inp.error) if part]
    return " ".join(parts)


def _best_topic_match(skill_levels: Mapping[str, float], prose_lower: str) -> str | None:
    """The best-matching skill key found in `prose_lower`, or None.

    A key matches if it (or its underscore-to-space form) appears in the
    prose on word boundaries. Several matches are broken by: longest key
    wins, then lowest skill, then alphabetical.
    """
    matches: list[str] = []
    for key in skill_levels:
        key_lower = key.lower()
        spaced = key_lower.replace("_", " ")
        if re.search(rf"\b{re.escape(key_lower)}\b", prose_lower):
            matches.append(key)
            continue
        if spaced != key_lower and re.search(rf"\b{re.escape(spaced)}\b", prose_lower):
            matches.append(key)

    if not matches:
        return None
    matches.sort(key=lambda k: (-len(k), skill_levels[k], k))
    return matches[0]


def analyze_problem(
    inp: StructuredInput | None,
    profile: LearnerProfileView,
    topic_hint: str | None = None,
) -> ProblemAnalysis:
    """Infer a topic and skill level for this turn.

    An explicit `topic_hint` always wins. Otherwise, the learner's known
    skill keys are matched against the prose of `inp` (question/problem/error
    only). If nothing matches, the topic is unknown and skill defaults to
    `PRIOR`.
    """
    if topic_hint is not None and topic_hint.strip():
        slug = slug_tag(topic_hint)
        return ProblemAnalysis(
            topic=slug,
            skill_level=profile.skill_levels.get(slug, PRIOR),
            topic_source="hint",
        )

    prose = _prose(inp).lower()
    if prose:
        matched = _best_topic_match(profile.skill_levels, prose)
        if matched is not None:
            return ProblemAnalysis(
                topic=matched,
                skill_level=profile.skill_levels[matched],
                topic_source="profile_match",
            )

    return ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")


def difficulty_for(skill: float) -> Difficulty:
    """Map a skill level in [0, 1] to a `Difficulty` bucket."""
    if skill < WEAK_SKILL:
        return "easy"
    if skill < HARD_SKILL:
        return "medium"
    return "hard"


def build_plan(
    intent: IntentResult | None,
    profile: LearnerProfileView,
    analysis: ProblemAnalysis,
) -> TeachingPlan:
    """Apply the deterministic rule ladder to produce this turn's `TeachingPlan`."""
    if intent is None or intent.low_confidence:
        rationale = ["no_intent"] if intent is None else ["low_confidence"]
        return TeachingPlan(
            difficulty=difficulty_for(analysis.skill_level),
            assistance_level="hint",
            solution_strategy="clarify",
            topic=analysis.topic,
            skill_level=analysis.skill_level,
            step_by_step=False,
            concise=False,
            watch_errors=profile.common_errors[:5],
            rationale=rationale,
        )

    assistance, strategy = INTENT_DEFAULTS[intent.intent]
    rationale: list[str] = []
    concise = False
    step_by_step = False
    prefers_hints = profile.learning_preferences.get("prefers_hints", False)

    if analysis.skill_level < WEAK_SKILL:
        assistance = "hint"
        rationale.append("weak_skill")

    if prefers_hints:
        assistance = "hint"
        rationale.append("prefers_hints")

    if analysis.skill_level >= STRONG_SKILL and not prefers_hints:
        next_index = min(ASSISTANCE_ORDER.index(assistance) + 1, len(ASSISTANCE_ORDER) - 1)
        assistance = ASSISTANCE_ORDER[next_index]
        concise = True
        rationale.append("strong_skill")

    if ASSISTANCE_ORDER.index(assistance) > ASSISTANCE_ORDER.index(MAX_INITIAL_ASSISTANCE):
        assistance = MAX_INITIAL_ASSISTANCE
        rationale.append("capped_initial_assistance")

    if profile.learning_preferences.get("likes_step_by_step", False):
        step_by_step = True
        concise = False
        rationale.append("likes_step_by_step")

    if (
        profile.learning_preferences.get("wants_line_by_line_explanations", False)
        and strategy == "step_by_step_explanation"
    ):
        step_by_step = True
        rationale.append("line_by_line")

    return TeachingPlan(
        difficulty=difficulty_for(analysis.skill_level),
        assistance_level=assistance,
        solution_strategy=strategy,
        topic=analysis.topic,
        skill_level=analysis.skill_level,
        step_by_step=step_by_step,
        concise=concise,
        watch_errors=profile.common_errors[:5],
        rationale=rationale,
    )
