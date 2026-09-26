"""Deterministic teaching planner: intent + learner profile -> `TeachingPlan`.

Pure and LLM-free by design (Phase 4 scope): every decision here is a fixed
rule applied to the classified intent, the learner profile, and a topic/skill
analysis of the current turn, so it is testable and auditable without a
model in the loop. The planner only ever proposes assistance up to
`MAX_INITIAL_ASSISTANCE` on an ordinary turn -- "full" is normally reached
only later, by the Phase 7 hint ladder as a learner works through
progressively stronger hints. The one exception is `build_plan`'s Packet P3
escalation rule: assistance may jump straight to "full" on a single turn, but
only when the learner has already been served this problem's hint-ladder
ceiling, explicitly asks for the solution, and has a sandbox-verified
pass/fail attempt on record for it. See `build_plan`'s docstring and the
block it guards for the exact rule.
"""

import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, Literal

# `HintProgress` is the hint-ladder's own conversation-state record (see
# `app.memory.hint_progress`); the planner only ever reads it, never writes
# it, to decide whether this turn may escalate past `MAX_INITIAL_ASSISTANCE`.
from app.agents.hint_engine import HintProgress

# `PRIOR` (the neutral starting skill for an unseen topic) is owned by the
# profile store; the planner reuses it rather than redefining its own.
from app.memory.profile import PRIOR
from app.schemas.agent_results import MAX_HINT_LEVEL_FOR_ASSISTANCE
from app.schemas.base import APIModel
from app.schemas.event import Difficulty, slug_tag
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, SolutionStrategy, TeachingPlan
from app.schemas.profile import LearnerProfileView

__all__ = [
    "DSA_ROUTE_INTENTS",
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

#: The intents `app.graph.routing.INTENT_ROUTES` sends to the DSA subgraph --
#: the only route with a hint ladder, and therefore the only route the
#: Packet P3 escalation-to-`full` rule ever applies to. Kept in lockstep with
#: that mapping deliberately (not imported from it): `app.graph.routing`
#: imports `app.graph.state`, and `app.agents.planner` must stay import-clean
#: of the graph package, so this is a small, intentional duplication rather
#: than a dependency in the wrong direction.
DSA_ROUTE_INTENTS: Final[frozenset[Intent]] = frozenset(
    {Intent.DSA_SOLVE, Intent.DSA_HINT, Intent.APPROACH_DISCUSSION}
)

#: Owner-approved policy (Packet P3): a narrow, deterministic phrase match for
#: "this turn explicitly asks for the solution/answer" -- never an LLM
#: judgement call over free-form learner text, which could be talked into
#: firing. Always combined with `intent.intent is Intent.DSA_SOLVE` (see
#: `_explicit_solution_request`): the classifier already reserves
#: `DSA_HINT` for an explicit hint/nudge ask, so this regex only needs to
#: separate a genuine "give me the answer" plea from `DSA_SOLVE`'s *other*
#: common case -- a bare first problem statement with no ask of its own at
#: all (see `app.input.intent.rule_intent`).
_EXPLICIT_ASK_RE: Final = re.compile(
    r"\b("
    r"just (?:give|tell|show) me|"
    r"give me the (?:full |complete |entire )?(?:solution|answer|code)|"
    r"(?:full|complete|entire) solution|"
    r"the answer|"
    r"solve (?:it|this) for me|"
    r"show me the (?:solution|code|answer)|"
    r"i give up|"
    r"tell me the answer"
    r")\b"
)


def _explicit_solution_request(
    intent: IntentResult, structured_input: StructuredInput | None
) -> bool:
    """This turn explicitly asks for the solution/answer -- condition 2 of 3.

    Deliberately requires BOTH the classifier's categorical `DSA_SOLVE`
    intent AND a narrow keyword match against the learner's own prose (never
    an LLM prompt over that prose -- see `_EXPLICIT_ASK_RE`'s docstring).
    """
    if intent.intent is not Intent.DSA_SOLVE:
        return False
    return bool(_EXPLICIT_ASK_RE.search(_prose(structured_input).lower()))


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
#: from "no question at all", measured against this corpus and reranker.
#:
#: Re-measured for Packet P2 (`app.graph.nodes.build_retrieval_query` now folds
#: in exception-type/identifier signal for debug/review turns -- see that
#: function's docstring), against the 30-probe set in
#: `tests/graph/test_topic_accuracy.py`:
#:
#:     real questions              weakest correct top score   -2.65
#:     bare follow-ups             strongest (still-empty) top score  -6.96
#:     off-corpus debug turn       top score  -5.94  (a `factorial` bug --
#:                                 no corpus pattern covers it; correctly
#:                                 belongs below the floor, not above it)
#:
#: -5.0 sits in the band between the weakest real question (-2.65) and the
#: strongest score that must still be rejected (-5.94), with margin on both
#: sides. This is *tighter* than the pre-P2 value (-6.0): richer,
#: identifier-bearing queries push genuine matches to clearly higher scores,
#: which leaves room to raise the floor and catch more no-real-topic turns
#: without losing any real one. It is corpus- and model-specific: re-measure
#: it if `reranker_model`, the corpus, or `build_retrieval_query` changes.
#: Below the floor the turn simply has no topic, and the hint ladder falls back
#: to the conversation's most recent one (`app.graph.nodes._hint_topic_key`),
#: which is the right behaviour for a follow-up.
MIN_RETRIEVAL_TOPIC_SCORE: Final = -5.0


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
    *,
    hint_progress: HintProgress | None = None,
    structured_input: StructuredInput | None = None,
) -> TeachingPlan:
    """Apply the deterministic rule ladder to produce this turn's `TeachingPlan`.

    `hint_progress` and `structured_input` feed only the Packet P3
    escalation rule below (see the block after `capped_initial_assistance`);
    every other rule in this function is unchanged and neither parameter is
    consulted before that point. Both default to `None` (treated as "no
    escalation possible") so every existing caller/test that doesn't pass
    them keeps its exact prior behaviour.
    """
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

    # --- Packet P3: server-enforced escalation past MAX_INITIAL_ASSISTANCE ---
    #
    # Owner-approved policy: assistance may rise to "full" (unlocking hint
    # ladder rung L6, and with it `DSAResult.code`) only when ALL THREE hold,
    # for THIS problem:
    #   1. ceiling reached  -- the learner has already been served the
    #      highest rung `assistance` (as computed above, before this block)
    #      allows,
    #   2. explicit ask     -- this turn is a genuine "give me the answer"
    #      plea, not just any `DSA_SOLVE` classification,
    #   3. demonstrated effort -- a sandbox actually ran this learner's code
    #      on this problem, earlier in its history, and returned pass/fail.
    #
    # Scoped to `DSA_ROUTE_INTENTS`: assistance escalation only has any
    # effect through the DSA hint ladder, so every other intent is left
    # exactly as today with no rationale noise. If any of the three is
    # missing, a distinct tag names which one -- the audit trail for "why
    # did/didn't it give the answer" -- even when the other two hold.
    if intent.intent in DSA_ROUTE_INTENTS:
        progress = hint_progress if hint_progress is not None else HintProgress()
        ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE[assistance]
        ceiling_reached = progress.last_level is not None and progress.last_level >= ceiling
        explicit_ask = _explicit_solution_request(intent, structured_input)
        verified_attempt = progress.has_verified_attempt

        if ceiling_reached and explicit_ask and verified_attempt:
            assistance = "full"
            rationale.append("escalated")
        elif not ceiling_reached:
            rationale.append("escalation_denied_ceiling_not_reached")
        elif not explicit_ask:
            rationale.append("escalation_denied_no_explicit_ask")
        else:
            rationale.append("escalation_denied_no_verified_attempt")

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
