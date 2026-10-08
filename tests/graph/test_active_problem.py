"""ADAPTIVE-upgrade P1: a conversation's ACTIVE PROBLEM carries across turns.

Covers the pure pieces -- which problem a turn is about (`resolve_problem_relation`),
how a follow-up inherits the stored statement (`inherit_active_problem`), what
gets stored afterwards (`active_problem_update`), topic inheritance in
`analyze_problem`, and the fixed per-ladder ceiling (`ladder_ceiling`). The
live end-to-end behaviour is measured by `eval.transcript_probes`.
"""

from app.agents.hint_engine import HintProgress, ladder_ceiling, next_hint
from app.agents.planner import analyze_problem
from app.graph.nodes import (
    _decide,  # pyright: ignore[reportPrivateUsage]
    _retrieve_knowledge_fallback,  # pyright: ignore[reportPrivateUsage]
    active_problem_update,
    inherit_active_problem,
    names_corpus_subject,
    problem_key,
    resolve_problem_relation,
)
from app.graph.state import AgentState, RawInput
from app.schemas.agent_results import HintLevel
from app.schemas.input import ActiveProblem, CodeBlock, StructuredInput
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView

_STATEMENT = "Given the root of a binary tree, return the maximum path sum of any path."


def _problem(question: str | None = None) -> StructuredInput:
    return StructuredInput(source="text", problem=_STATEMENT, question=question)


def _active(topic: str | None = "trees") -> ActiveProblem:
    key = problem_key(_problem())
    assert key is not None
    return ActiveProblem(problem=_problem(), key=key, topic=topic)


def _plan(level: str = "concept", rationale: list[str] | None = None) -> TeachingPlan:
    return TeachingPlan(
        difficulty="medium",
        assistance_level=level,  # pyright: ignore[reportArgumentType]
        solution_strategy="socratic_hints",
        topic="trees",
        skill_level=0.5,
        rationale=rationale or [],
    )


# --- problem_key -----------------------------------------------------------


def test_problem_key_ignores_the_direct_ask_line() -> None:
    """F3: re-pasting a statement with "give full code" must hit the same ladder."""
    assert problem_key(_problem()) == problem_key(_problem("give full code"))
    assert problem_key(_problem()) is not None
    assert not (problem_key(_problem()) or "").count(" ")


def test_problem_key_is_none_without_a_statement() -> None:
    assert problem_key(StructuredInput(source="text", question="next hint")) is None
    assert problem_key(None) is None


# --- resolve_problem_relation ---------------------------------------------


def test_new_statement_is_new() -> None:
    relation, key = resolve_problem_relation(_problem(), None)
    assert relation == "new"
    assert key == problem_key(_problem())


def test_repasted_statement_is_same() -> None:
    relation, key = resolve_problem_relation(_problem("give full code"), _active())
    assert (relation, key) == ("same", _active().key)


def test_repaste_with_the_ask_kept_inside_the_statement_is_same() -> None:
    """Normalization can leave a trailing "give full code" inside `problem` (T10)."""
    repaste = StructuredInput(source="text", problem=_STATEMENT + " give full code")
    assert resolve_problem_relation(repaste, _active()) == ("same", _active().key)


def test_bare_followups_inherit() -> None:
    for text in ("give full answer", "Give me the next hint.", "give code for that", "why?"):
        followup = StructuredInput(source="text", question=text)
        assert resolve_problem_relation(followup, _active()) == ("followup", _active().key), text


def test_a_turn_naming_a_corpus_subject_is_its_own_question() -> None:
    for text in ("what is a trie?", "explain dynamic programming", "how does a min heap work"):
        question = StructuredInput(source="text", question=text)
        assert resolve_problem_relation(question, _active()) == ("none", None), text


def test_code_without_statement_never_inherits() -> None:
    """Attaching the active statement to unrelated code would let it judge that code."""
    code = StructuredInput(
        source="text", question="fix this", code=[CodeBlock(content="def f(n): return n")]
    )
    assert resolve_problem_relation(code, _active()) == ("none", None)


def test_no_active_problem_means_none() -> None:
    followup = StructuredInput(source="text", question="next hint")
    assert resolve_problem_relation(followup, None) == ("none", None)


def test_corpus_vocabulary_matches_whole_words_only() -> None:
    assert names_corpus_subject("use a heap here")
    assert names_corpus_subject("BFS please")
    assert not names_corpus_subject("give full answer")
    assert not names_corpus_subject("hi")


# --- inherit_active_problem -----------------------------------------------


def test_inherit_keeps_the_followup_question_and_the_stored_statement() -> None:
    followup = StructuredInput(source="text", question="give full answer")
    merged = inherit_active_problem(followup, _active())
    assert merged.problem == _STATEMENT
    assert merged.question == "give full answer"
    assert merged.code == []


# --- analyze_problem ------------------------------------------------------


def test_inherited_topic_wins_over_retrieval_and_profile() -> None:
    """F2: "give full answer" on a trees problem must not drift to heaps."""
    profile = LearnerProfileView.empty().model_copy(update={"skill_levels": {"heaps": 0.5}})
    analysis = analyze_problem(
        StructuredInput(source="text", question="heaps answer please"),
        profile,
        inherited_topic="trees",
    )
    assert analysis.topic == "trees"
    assert analysis.topic_source == "conversation"


def test_explicit_hint_still_beats_inherited_topic() -> None:
    analysis = analyze_problem(
        None, LearnerProfileView.empty(), topic_hint="graphs", inherited_topic="trees"
    )
    assert analysis.topic == "graphs"


# --- active_problem_update ------------------------------------------------


def _state(**update: object) -> AgentState:
    return AgentState(input=RawInput(text="x")).model_copy(update=update)


def test_new_problem_is_stored_with_its_trusted_topic_and_without_code() -> None:
    inp = _problem().model_copy(update={"code": [CodeBlock(content="x = 1")]})
    state = _state(
        structured_input=inp,
        problem_relation="new",
        problem_key=problem_key(inp),
        plan=_plan(),
        topic_source="retrieval",
    )
    stored = active_problem_update(state)
    assert stored is not None
    assert stored.topic == "trees"
    assert stored.problem.code == []


def test_client_topic_hint_is_not_stored_as_the_problem_topic() -> None:
    state = _state(
        structured_input=_problem(),
        problem_relation="new",
        problem_key=problem_key(_problem()),
        plan=_plan(),
        topic_source="hint",
    )
    stored = active_problem_update(state)
    assert stored is not None
    assert stored.topic is None


def test_followup_backfills_a_missing_topic_only() -> None:
    state = _state(
        problem_relation="followup",
        active_problem=_active(topic=None),
        plan=_plan(),
        topic_source="retrieval",
    )
    stored = active_problem_update(state)
    assert stored is not None
    assert stored.topic == "trees"
    assert active_problem_update(state.model_copy(update={"active_problem": _active()})) is None


def test_no_problem_in_play_leaves_the_store_alone() -> None:
    assert active_problem_update(_state(problem_relation="none", plan=_plan())) is None


# --- fixed ladder ceiling -------------------------------------------------


def test_ladder_keeps_its_stored_ceiling_across_intents() -> None:
    """F3: "next hint" (DSA_HINT -> hint, ceiling L2) on a ladder started at
    concept (L3) must still report N = L3, not shrink to L2."""
    progress = HintProgress(last_level=HintLevel.L0_NUDGE, ceiling=HintLevel.L3_CONCRETE_IDEA)
    assert ladder_ceiling(_plan("hint"), progress) == HintLevel.L3_CONCRETE_IDEA
    hint = next_hint(None, _plan("hint"), progress)
    assert hint is not None
    assert hint.ceiling == HintLevel.L3_CONCRETE_IDEA
    assert hint.level == HintLevel.L1_WHAT_TO_TRACK


def test_client_cap_may_still_lower_the_ceiling() -> None:
    progress = HintProgress(ceiling=HintLevel.L3_CONCRETE_IDEA)
    capped = _plan("hint", rationale=["assistance_capped"])
    assert ladder_ceiling(capped, progress) == HintLevel.L2_DATA_STRUCTURE


def test_escalation_to_full_overrides_the_stored_ceiling() -> None:
    progress = HintProgress(ceiling=HintLevel.L3_CONCRETE_IDEA)
    assert ladder_ceiling(_plan("full"), progress) == HintLevel.L6_FULL


def test_new_ladder_uses_this_turns_ceiling() -> None:
    assert ladder_ceiling(_plan("concept"), HintProgress()) == HintLevel.L3_CONCRETE_IDEA


# --- code-review fixes (P1) -----------------------------------------------


def test_error_only_turn_keeps_its_traceback() -> None:
    """A pasted traceback with no statement is the debugger's input, not a follow-up."""
    error = StructuredInput(source="text", error="IndexError: list index out of range")
    assert resolve_problem_relation(error, _active()) == ("none", None)


def test_a_longer_variant_of_the_statement_is_a_new_problem() -> None:
    variant = StructuredInput(
        source="text",
        problem=_STATEMENT + " Now the tree may contain up to 10^6 nodes and every value is "
        "negative, and you must also return the path itself, not just its sum.",
    )
    relation, key = resolve_problem_relation(variant, _active())
    assert relation == "new"
    assert key != _active().key


def test_the_decision_settles_continuity_before_retrieval() -> None:
    """Which problem a turn is about is decided by `decide_turn`, before
    retrieval runs, so Qdrant being down cannot cost a follow-up its problem."""
    state = AgentState(
        input=RawInput(text="give full answer"),
        structured_input=StructuredInput(source="text", question="give full answer"),
        active_problem=_active(),
    )
    assert _retrieve_knowledge_fallback(state) == {"retrieved_context": []}
    update = _decide(state)
    assert update.get("problem_relation") == "followup"
    assert update.get("problem_key") == _active().key
    inherited = update.get("structured_input")
    assert inherited is not None
    assert inherited.problem == _STATEMENT


def test_an_explicit_ask_follow_up_is_classified_as_a_solution_request() -> None:
    """P4 / T9: "give code for that" on the active problem is DSA_SOLVE, not a
    low-confidence concept question routed to clarify."""
    from app.schemas.intent import Intent, IntentResult

    state = AgentState(
        input=RawInput(text="give code for that"),
        structured_input=StructuredInput(source="text", question="give code for that"),
        active_problem=_active(),
        intent=IntentResult(intent=Intent.CONCEPT_EXPLANATION, confidence=0.4, source="llm"),
    )
    update = _decide(state)
    intent = update.get("intent")
    assert intent is not None
    assert intent.intent is Intent.DSA_SOLVE
    assert not intent.low_confidence


def test_a_plain_follow_up_keeps_its_own_intent() -> None:
    from app.schemas.intent import Intent, IntentResult

    state = AgentState(
        input=RawInput(text="why?"),
        structured_input=StructuredInput(source="text", question="why does that work?"),
        active_problem=_active(),
        intent=IntentResult(intent=Intent.APPROACH_DISCUSSION, confidence=0.9, source="llm"),
    )
    assert "intent" not in _decide(state)
