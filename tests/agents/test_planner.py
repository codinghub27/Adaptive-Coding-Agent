"""Pure unit tests for the deterministic teaching planner.

No database, no LLM, no markers: these must always run.
"""

from itertools import product

import pytest

from app.agents.planner import (
    HARD_SKILL,
    INTENT_DEFAULTS,
    MIN_RETRIEVAL_TOPIC_SCORE,
    STRONG_SKILL,
    WEAK_SKILL,
    ProblemAnalysis,
    analyze_problem,
    build_plan,
    clamp_assistance,
    difficulty_for,
)
from app.memory.profile import PRIOR
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, TeachingPlan
from app.schemas.profile import LearnerProfileView


def _profile(
    skill_levels: dict[str, float] | None = None,
    prefs: dict[str, bool] | None = None,
    common_errors: list[str] | None = None,
) -> LearnerProfileView:
    return LearnerProfileView(
        language=None,
        skill_levels=skill_levels or {},
        learning_preferences=prefs or {},
        common_errors=common_errors or [],
    )


def _intent(intent: Intent, confidence: float = 0.9) -> IntentResult:
    return IntentResult(intent=intent, confidence=confidence, source="rule")


def _hit(*, topic: str, pattern: str, score: float = 0.9, reranked: bool = True) -> RetrievalHit:
    chunk = KnowledgeChunk(
        id="chunk-1",
        text="chunk text",
        source="source.md",
        title="Title",
        heading="Heading",
        topic=topic,
        pattern=pattern,
    )
    return RetrievalHit(chunk=chunk, score=score, retrievers=("bm25",), reranked=reranked)


def _input(
    question: str | None = None,
    problem: str | None = None,
    error: str | None = None,
    code: list[CodeBlock] | None = None,
) -> StructuredInput:
    return StructuredInput(
        source="text", question=question, problem=problem, error=error, code=code or []
    )


# ---------------------------------------------------------------------------
# INTENT_DEFAULTS
# ---------------------------------------------------------------------------


def test_intent_defaults_covers_every_intent() -> None:
    assert set(INTENT_DEFAULTS) == set(Intent)


@pytest.mark.parametrize(
    ("intent", "assistance", "strategy"),
    [
        (Intent.DSA_HINT, "hint", "socratic_hints"),
        (Intent.DSA_SOLVE, "concept", "socratic_hints"),
        (Intent.APPROACH_DISCUSSION, "concept", "socratic_hints"),
        (Intent.CODE_DEBUG, "hint", "guided_debugging"),
        (Intent.ERROR_EXPLANATION, "concept", "guided_debugging"),
        (Intent.TEST_CASE_ANALYSIS, "hint", "guided_debugging"),
        (Intent.CODE_EXPLAIN, "concept", "step_by_step_explanation"),
        (Intent.CONCEPT_EXPLANATION, "concept", "step_by_step_explanation"),
        (Intent.IMAGE_CODE_ANALYSIS, "concept", "step_by_step_explanation"),
        (Intent.CODE_REVIEW, "concept", "concise_review"),
        (Intent.OPTIMIZATION, "concept", "concise_review"),
    ],
)
def test_intent_defaults_values(intent: Intent, assistance: str, strategy: str) -> None:
    assert INTENT_DEFAULTS[intent] == (assistance, strategy)


# ---------------------------------------------------------------------------
# analyze_problem
# ---------------------------------------------------------------------------


def test_analyze_problem_hint_wins_over_prose_match() -> None:
    profile = _profile(skill_levels={"sliding_window": 0.3, "arrays": 0.9})
    inp = _input(question="explain arrays please")
    result = analyze_problem(inp, profile, topic_hint="Sliding Window")
    assert result == ProblemAnalysis(topic="sliding_window", skill_level=0.3, topic_source="hint")


def test_analyze_problem_hint_slug_normalization_unseen_topic() -> None:
    profile = _profile(skill_levels={})
    result = analyze_problem(None, profile, topic_hint=" Two Pointers ")
    assert result == ProblemAnalysis(topic="two_pointers", skill_level=PRIOR, topic_source="hint")


def test_analyze_problem_blank_hint_falls_through_to_prose_match() -> None:
    profile = _profile(skill_levels={"arrays": 0.7})
    inp = _input(question="I need help with arrays")
    result = analyze_problem(inp, profile, topic_hint="   ")
    assert result == ProblemAnalysis(topic="arrays", skill_level=0.7, topic_source="profile_match")


def test_analyze_problem_spaced_phrase_matches_underscored_key() -> None:
    profile = _profile(skill_levels={"sliding_window": 0.6})
    inp = _input(question="This looks like a sliding window problem")
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(
        topic="sliding_window", skill_level=0.6, topic_source="profile_match"
    )


def test_analyze_problem_code_only_match_does_not_count() -> None:
    profile = _profile(skill_levels={"recursion": 0.5})
    inp = _input(
        question="Fix this function",
        code=[CodeBlock(content="def recursion(): pass", language="python")],
    )
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")


def test_analyze_problem_longest_key_wins_on_multiple_matches() -> None:
    profile = _profile(skill_levels={"sort": 0.2, "merge_sort": 0.9})
    inp = _input(question="Please explain merge sort algorithm steps")
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(
        topic="merge_sort", skill_level=0.9, topic_source="profile_match"
    )


def test_analyze_problem_length_tie_breaks_on_lowest_skill() -> None:
    profile = _profile(skill_levels={"abc": 0.5, "xyz": 0.2})
    inp = _input(question="abc xyz problem")
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(topic="xyz", skill_level=0.2, topic_source="profile_match")


def test_analyze_problem_skill_tie_breaks_alphabetically() -> None:
    profile = _profile(skill_levels={"bbb": 0.3, "aaa": 0.3})
    inp = _input(question="aaa bbb text")
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(topic="aaa", skill_level=0.3, topic_source="profile_match")


def test_analyze_problem_no_input_is_unknown() -> None:
    profile = _profile(skill_levels={"arrays": 0.7})
    result = analyze_problem(None, profile)
    assert result == ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")


def test_analyze_problem_no_prose_match_is_unknown() -> None:
    profile = _profile(skill_levels={"arrays": 0.7})
    inp = _input(question="something unrelated entirely")
    result = analyze_problem(inp, profile)
    assert result == ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")


# ---------------------------------------------------------------------------
# analyze_problem -- retrieval-derived topic (Packet B)
# ---------------------------------------------------------------------------


def test_analyze_problem_uses_top_retrieved_hit_with_empty_profile() -> None:
    profile = _profile(skill_levels={})
    hit = _hit(topic="arrays", pattern="two_pointers")
    result = analyze_problem(None, profile, context=[hit])
    assert result == ProblemAnalysis(
        topic="two_pointers", skill_level=PRIOR, topic_source="retrieval"
    )


def test_analyze_problem_hint_wins_over_retrieval() -> None:
    profile = _profile(skill_levels={})
    hit = _hit(topic="arrays", pattern="two_pointers")
    result = analyze_problem(None, profile, topic_hint="hashing", context=[hit])
    assert result == ProblemAnalysis(topic="hashing", skill_level=PRIOR, topic_source="hint")


def test_analyze_problem_profile_match_wins_over_retrieval() -> None:
    profile = _profile(skill_levels={"arrays": 0.6})
    inp = _input(question="explain arrays please")
    hit = _hit(topic="graphs", pattern="bfs")
    result = analyze_problem(inp, profile, context=[hit])
    assert result == ProblemAnalysis(topic="arrays", skill_level=0.6, topic_source="profile_match")


def test_analyze_problem_retrieval_beats_nothing() -> None:
    profile = _profile(skill_levels={})
    hit = _hit(topic="arrays", pattern="two_pointers")
    result = analyze_problem(None, profile, context=[hit])
    assert result.topic == "two_pointers"
    assert result.topic_source == "retrieval"


def test_analyze_problem_empty_context_and_profile_is_unknown() -> None:
    profile = _profile(skill_levels={})
    result = analyze_problem(None, profile, context=[])
    assert result == ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")


def test_analyze_problem_retrieval_topic_uses_existing_skill_level() -> None:
    profile = _profile(skill_levels={"two_pointers": 0.8})
    hit = _hit(topic="arrays", pattern="two_pointers")
    result = analyze_problem(None, profile, context=[hit])
    assert result == ProblemAnalysis(
        topic="two_pointers", skill_level=0.8, topic_source="retrieval"
    )


def test_analyze_problem_retrieval_topic_falls_back_to_prior_when_unseen() -> None:
    profile = _profile(skill_levels={"arrays": 0.9})
    hit = _hit(topic="graphs", pattern="bfs")
    result = analyze_problem(None, profile, context=[hit])
    assert result == ProblemAnalysis(topic="bfs", skill_level=PRIOR, topic_source="retrieval")


def test_analyze_problem_retrieval_ignores_negative_rerank_score() -> None:
    """A raw cross-encoder rerank score can be negative even for a correct
    match; `analyze_problem` trusts the retriever's rank ordering, not the
    sign or magnitude of `score` (see the `analyze_problem` docstring)."""
    profile = _profile(skill_levels={})
    hit = _hit(topic="arrays", pattern="two_pointers", score=-2.45)
    result = analyze_problem(None, profile, context=[hit])
    assert result.topic == "two_pointers"
    assert result.topic_source == "retrieval"


def test_analyze_problem_retrieval_uses_only_the_top_hit() -> None:
    top = _hit(topic="arrays", pattern="two_pointers")
    second = _hit(topic="graphs", pattern="bfs")
    profile = _profile(skill_levels={})
    result = analyze_problem(None, profile, context=[top, second])
    assert result.topic == "two_pointers"


# ---------------------------------------------------------------------------
# difficulty_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("skill", "expected"),
    [
        (0.39, "easy"),
        (0.4, "medium"),
        (0.69, "medium"),
        (0.7, "hard"),
    ],
)
def test_difficulty_for_boundaries(skill: float, expected: str) -> None:
    assert difficulty_for(skill) == expected


def test_weak_strong_hard_skill_constants() -> None:
    assert WEAK_SKILL == 0.4
    assert STRONG_SKILL == 0.75
    assert HARD_SKILL == 0.7


# ---------------------------------------------------------------------------
# build_plan
# ---------------------------------------------------------------------------


def test_build_plan_manual_test_1_weak_skill_and_prefers_hints() -> None:
    profile = _profile(skill_levels={"sliding_window": 0.3}, prefs={"prefers_hints": True})
    inp = _input(question="I have a bug in my sliding window solution")
    analysis = analyze_problem(inp, profile)
    intent = _intent(Intent.CODE_DEBUG)

    plan = build_plan(intent, profile, analysis)

    assert plan.assistance_level == "hint"
    assert plan.solution_strategy == "guided_debugging"
    assert plan.difficulty == "easy"
    assert plan.topic == "sliding_window"
    assert "weak_skill" in plan.rationale
    assert "prefers_hints" in plan.rationale


def test_build_plan_strong_skill_without_prefers_hints_raises_and_conciseness() -> None:
    profile = _profile(skill_levels={}, prefs={})
    analysis = ProblemAnalysis(topic="arrays", skill_level=0.9, topic_source="profile_match")
    intent = _intent(Intent.CODE_EXPLAIN)

    plan = build_plan(intent, profile, analysis)

    assert plan.assistance_level == "pseudocode"
    assert plan.concise is True
    assert "strong_skill" in plan.rationale


def test_build_plan_strong_skill_with_prefers_hints_stays_hint() -> None:
    profile = _profile(skill_levels={}, prefs={"prefers_hints": True})
    analysis = ProblemAnalysis(topic="arrays", skill_level=0.9, topic_source="profile_match")
    intent = _intent(Intent.DSA_HINT)

    plan = build_plan(intent, profile, analysis)

    assert plan.assistance_level == "hint"
    assert "prefers_hints" in plan.rationale
    assert "strong_skill" not in plan.rationale
    assert "weak_skill" not in plan.rationale


@pytest.mark.parametrize(
    ("intent", "skill", "prefers_hints", "likes_step_by_step", "wants_line_by_line"),
    list(product(list(Intent), [0.1, 0.5, 0.95], [False, True], [False, True], [False, True])),
)
def test_build_plan_never_yields_full_on_first_turn(
    intent: Intent,
    skill: float,
    prefers_hints: bool,
    likes_step_by_step: bool,
    wants_line_by_line: bool,
) -> None:
    profile = _profile(
        skill_levels={},
        prefs={
            "prefers_hints": prefers_hints,
            "likes_step_by_step": likes_step_by_step,
            "wants_line_by_line_explanations": wants_line_by_line,
        },
    )
    analysis = ProblemAnalysis(topic=None, skill_level=skill, topic_source="unknown")
    intent_result = _intent(intent)

    plan = build_plan(intent_result, profile, analysis)

    assert plan.assistance_level != "full"


def test_build_plan_low_confidence_yields_clarify() -> None:
    profile = _profile()
    analysis = ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")
    intent = _intent(Intent.DSA_SOLVE, confidence=0.3)

    plan = build_plan(intent, profile, analysis)

    assert plan.assistance_level == "hint"
    assert plan.solution_strategy == "clarify"
    assert plan.rationale == ["low_confidence"]


def test_build_plan_no_intent_yields_clarify() -> None:
    profile = _profile()
    analysis = ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")

    plan = build_plan(None, profile, analysis)

    assert plan.assistance_level == "hint"
    assert plan.solution_strategy == "clarify"
    assert plan.rationale == ["no_intent"]


def test_build_plan_likes_step_by_step_overrides_concise() -> None:
    profile = _profile(skill_levels={}, prefs={"likes_step_by_step": True})
    analysis = ProblemAnalysis(topic="arrays", skill_level=0.9, topic_source="profile_match")
    intent = _intent(Intent.CODE_EXPLAIN)

    plan = build_plan(intent, profile, analysis)

    assert plan.step_by_step is True
    assert plan.concise is False
    assert "strong_skill" in plan.rationale
    assert "likes_step_by_step" in plan.rationale


def test_build_plan_line_by_line_requires_step_by_step_explanation_strategy() -> None:
    profile = _profile(skill_levels={}, prefs={"wants_line_by_line_explanations": True})
    analysis = ProblemAnalysis(topic="arrays", skill_level=0.5, topic_source="profile_match")
    intent = _intent(Intent.CODE_EXPLAIN)

    plan = build_plan(intent, profile, analysis)

    assert plan.step_by_step is True
    assert "line_by_line" in plan.rationale


def test_build_plan_line_by_line_does_not_fire_for_other_strategies() -> None:
    profile = _profile(skill_levels={}, prefs={"wants_line_by_line_explanations": True})
    analysis = ProblemAnalysis(topic="arrays", skill_level=0.5, topic_source="profile_match")
    intent = _intent(Intent.CODE_REVIEW)

    plan = build_plan(intent, profile, analysis)

    assert plan.step_by_step is False
    assert "line_by_line" not in plan.rationale


def test_build_plan_watch_errors_truncated_to_five() -> None:
    errors = ["e1", "e2", "e3", "e4", "e5", "e6", "e7"]
    profile = _profile(skill_levels={}, prefs={}, common_errors=errors)
    analysis = ProblemAnalysis(topic=None, skill_level=PRIOR, topic_source="unknown")
    intent = _intent(Intent.CODE_REVIEW)

    plan = build_plan(intent, profile, analysis)

    assert plan.watch_errors == ["e1", "e2", "e3", "e4", "e5"]


# ---------------------------------------------------------------------------
# clamp_assistance
# ---------------------------------------------------------------------------


def _plan(assistance_level: AssistanceLevel) -> TeachingPlan:
    return TeachingPlan(
        difficulty="medium",
        assistance_level=assistance_level,
        solution_strategy="socratic_hints",
        topic=None,
        skill_level=0.5,
        rationale=["some_rule"],
    )


def test_clamp_assistance_none_cap_is_a_no_op() -> None:
    plan = _plan("full")
    assert clamp_assistance(plan, None) is plan


def test_clamp_assistance_equal_cap_is_a_no_op() -> None:
    plan = _plan("partial")
    result = clamp_assistance(plan, "partial")
    assert result is plan


def test_clamp_assistance_higher_cap_is_a_no_op() -> None:
    plan = _plan("hint")
    result = clamp_assistance(plan, "full")
    assert result is plan


def test_clamp_assistance_lower_cap_lowers_level_and_adds_rationale() -> None:
    plan = _plan("full")
    result = clamp_assistance(plan, "concept")
    assert result.assistance_level == "concept"
    assert result.rationale == ["some_rule", "assistance_capped"]
    # The original plan is untouched (pydantic models here aren't frozen,
    # but `clamp_assistance` must still return a copy, not mutate in place).
    assert plan.assistance_level == "full"
    assert plan.rationale == ["some_rule"]


@pytest.mark.parametrize(("level", "cap"), list(product(ASSISTANCE_ORDER, ASSISTANCE_ORDER)))
def test_clamp_assistance_is_monotonic_for_every_pair(
    level: AssistanceLevel, cap: AssistanceLevel
) -> None:
    plan = _plan(level)
    result = clamp_assistance(plan, cap)
    assert ASSISTANCE_ORDER.index(result.assistance_level) <= ASSISTANCE_ORDER.index(level)
    if ASSISTANCE_ORDER.index(cap) >= ASSISTANCE_ORDER.index(level):
        assert result.assistance_level == level
    else:
        assert result.assistance_level == cap


# --------------------------------------------------------------------------
# MIN_RETRIEVAL_TOPIC_SCORE: a turn that names no subject has no topic
# --------------------------------------------------------------------------


def test_retrieval_topic_ignored_when_the_top_hit_scores_below_the_floor() -> None:
    """A bare follow-up ("Give me the next hint.") still gets chunks back --
    the retriever always returns its best four -- but they are noise. Adopting
    one restarted the ladder on an unrelated subject and wrote a bogus skill
    into the profile, so below the floor the turn simply has no topic."""
    analysis = analyze_problem(
        _input("Give me the next hint."),
        LearnerProfileView.empty(),
        None,
        [_hit(topic="trees", pattern="trees", score=-8.39)],
    )

    assert analysis.topic is None
    assert analysis.topic_source == "unknown"
    assert analysis.skill_level == PRIOR


def test_retrieval_topic_used_at_the_floor_exactly() -> None:
    analysis = analyze_problem(
        _input("two sum problem"),
        LearnerProfileView.empty(),
        None,
        [_hit(topic="arrays", pattern="two_pointers", score=MIN_RETRIEVAL_TOPIC_SCORE)],
    )

    assert analysis.topic == "two_pointers"
    assert analysis.topic_source == "retrieval"


def test_the_floor_admits_every_measured_real_question_score() -> None:
    """Measured against this corpus and reranker, real questions scored -2.15,
    -3.25 and +6.63; bare follow-ups scored -8.39, -10.09 and -10.89. The floor
    must sit in the empty band between those clusters -- this pins that, so a
    future change to the constant cannot silently start dropping real topics."""
    real_question_scores = (-2.15, -3.25, 6.63)
    bare_followup_scores = (-8.39, -10.09, -10.89)

    assert all(score >= MIN_RETRIEVAL_TOPIC_SCORE for score in real_question_scores)
    assert all(score < MIN_RETRIEVAL_TOPIC_SCORE for score in bare_followup_scores)


def test_a_profile_match_still_wins_over_a_high_scoring_retrieval_hit() -> None:
    """The floor only gates the retrieval branch; it must not disturb the
    precedence order above it."""
    profile = LearnerProfileView(
        language=None,
        skill_levels={"binary_search": 0.8},
        learning_preferences={},
        common_errors=[],
    )
    analysis = analyze_problem(
        _input("help me with binary search"),
        profile,
        None,
        [_hit(topic="arrays", pattern="two_pointers", score=9.0)],
    )

    assert analysis.topic == "binary_search"
    assert analysis.topic_source == "profile_match"
