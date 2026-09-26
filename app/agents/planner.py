"""Deterministic teaching planner: intent + learner profile -> `TeachingPlan`.

Pure and LLM-free by design (Phase 4 scope): every decision here is a fixed
rule applied to the classified intent, the learner profile, and a topic/skill
analysis of the current turn, so it is testable and auditable without a
model in the loop. The planner only ever proposes assistance up to
`MAX_INITIAL_ASSISTANCE` on a first turn -- "full" is reached later, by the
Phase 7 hint ladder as a learner works through progressively stronger hints.
"""

import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, Literal

# `PRIOR` (the neutral starting skill for an unseen topic) is owned by the
# profile store; the planner reuses it rather than redefining its own.
from app.memory.profile import PRIOR
from app.schemas.base import APIModel
from app.schemas.event import Difficulty, slug_tag
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import RetrievalHit
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
    "clamp_assistance",
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
    topic_source: Literal["hint", "profile_match", "retrieval", "unknown"]


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


#: Minimum reranker score for a retrieved chunk to be trusted as *this turn's*
#: topic.
#:
#: The retriever always returns its best four chunks, so a turn that names no
#: subject at all ("Give me the next hint.") still gets an answer back -- just a
#: meaningless one. Adopting it as the topic restarts the hint ladder on an
#: unrelated subject and writes a bogus skill into the learner's profile, which
#: is exactly what happened in testing: "Give me the next hint." came back as
#: `trees`.
#:
#: This is NOT an absolute relevance cutoff -- the cross-encoder emits raw
#: logits that are routinely negative for correct matches, so `score > 0` would
#: throw away most good answers. It is calibrated to separate "a real question"
#: from "no question at all", measured against this corpus and reranker:
#:
#:     real questions   top score  -2.15, -3.25, +6.63
#:     bare follow-ups  top score  -8.39, -10.09, -10.89
#:
#: -6.0 sits in the empty band between those two clusters. It is corpus- and
#: model-specific: re-measure it if `reranker_model` or the corpus changes.
#: Below the floor the turn simply has no topic, and the hint ladder falls back
#: to the conversation's most recent one (`app.graph.nodes._hint_topic_key`),
#: which is the right behaviour for a follow-up.
MIN_RETRIEVAL_TOPIC_SCORE: Final = -6.0


def analyze_problem(
    inp: StructuredInput | None,
    profile: LearnerProfileView,
    topic_hint: str | None = None,
    context: Sequence[RetrievalHit] = (),
) -> ProblemAnalysis:
    """Infer a topic and skill level for this turn.

    Resolution order, first match wins:
    1. an explicit `topic_hint` ("hint"),
    2. the learner's known skill keys matched against the prose of `inp`
       (question/problem/error only) ("profile_match"),
    3. the top-ranked hit in `context`, the knowledge corpus chunks retrieved
       for this turn ("retrieval"),
    4. otherwise the topic is unknown and skill defaults to `PRIOR`
       ("unknown").

    `context` is assumed already ordered best-first by the retriever (see
    `app.knowledge.retrieve.Retriever`), so only `context[0]` is ever
    consulted -- its `pattern` is preferred, falling back to its `topic` if
    `pattern` is somehow less specific. No relevance-score floor is applied:
    the reranker returns raw cross-encoder logits, which are frequently
    negative even for a correct match (e.g. a correct Two Sum hit scored
    -2.45 in a live probe), so a naive `score > 0` cutoff would reject most
    correct answers. There is no principled threshold derivable from that
    distribution, so this deliberately trusts rank alone (the retriever's own
    fusion + rerank already chose the best hit) rather than inventing one.

    `chunk.topic`/`chunk.pattern` are corpus metadata validated through
    `slug_tag` at ingestion time (`app.schemas.knowledge.KnowledgeChunk`) --
    they come from the curated corpus, not from learner-authored input, so
    (unlike raw prose) they are trusted as-is: safe to use as a skill-map key
    and to compose directly into hint text.
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

    if context and context[0].score >= MIN_RETRIEVAL_TOPIC_SCORE:
        chunk = context[0].chunk
        slug = chunk.pattern or chunk.topic
        return ProblemAnalysis(
            topic=slug,
            skill_level=profile.skill_levels.get(slug, PRIOR),
            topic_source="retrieval",
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


def clamp_assistance(plan: TeachingPlan, cap: AssistanceLevel | None) -> TeachingPlan:
    """Apply a client-requested assistance ceiling to `plan`, never raising it.

    Monotonic-safety property: this function can only ever lower (or leave
    unchanged) `plan.assistance_level`. It never raises it, regardless of
    `cap`'s value -- so a hostile client cannot pass a high `cap` to extract
    more help than the planner itself decided on. `plan` is returned
    unchanged when `cap is None` (no ceiling requested) or when `cap` is at
    or above `plan.assistance_level` in `ASSISTANCE_ORDER` (the ceiling
    doesn't bind). Otherwise, a copy of `plan` is returned with
    `assistance_level` lowered to `cap` and `"assistance_capped"` appended to
    `rationale`.
    """
    if cap is None:
        return plan
    if ASSISTANCE_ORDER.index(cap) >= ASSISTANCE_ORDER.index(plan.assistance_level):
        return plan
    return plan.model_copy(
        update={
            "assistance_level": cap,
            "rationale": [*plan.rationale, "assistance_capped"],
        }
    )
