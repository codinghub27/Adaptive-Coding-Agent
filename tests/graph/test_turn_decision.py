"""The one tutoring decision per turn (`decide_turn`, target behaviour section 26).

Evidence behind these (docs/BEHAVIOR_GAP.md): 22 of 39 bad turns in the
owner's real conversations came from the turn's subject and action being
worked out in several places, each from the classifier's label. When that
label was unsure the turn fell to "could you confirm?" or to the wrong
subject. Reported on the current code: "give code using stack", on an open
problem, answered "It looks like you might want to explain a concept".
"""

from datetime import UTC, datetime

from app.graph.nodes import (
    _decide,  # pyright: ignore[reportPrivateUsage]
    _graded_evidence,  # pyright: ignore[reportPrivateUsage]
    clarify,
)
from app.graph.state import AgentState, GraphContext, RawInput
from app.schemas.decision import TurnDecision, scaffold_for
from app.schemas.input import ActiveProblem, CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.tutoring import AnswerGrade, PendingCheck, SessionProgress
from tests.input.fakes import FakeLLMClient

_P678 = (
    "678. Valid Parenthesis String\n\nGiven a string s containing only three types of "
    "characters: '(', ')' and '*', return true if s is valid."
)


class _Runtime:
    def __init__(self) -> None:
        self.context = GraphContext(llm=FakeLLMClient())


def _active() -> ActiveProblem:
    return ActiveProblem(
        problem=StructuredInput(source="image", problem=_P678), key="_p678", topic="stack"
    )


def _unsure(intent: Intent = Intent.CONCEPT_EXPLANATION) -> IntentResult:
    """What the keyword fallback returns when no model read the turn."""
    return IntentResult(intent=intent, confidence=0.2, source="fallback")


def _sure(intent: Intent, **flags: bool | str | None) -> IntentResult:
    return IntentResult.model_validate(
        {"intent": intent, "confidence": 0.9, "source": "llm", **flags}
    )


def _state(
    question: str,
    *,
    intent: IntentResult | None,
    active: ActiveProblem | None = None,
    progress: SessionProgress | None = None,
    pending: PendingCheck | None = None,
    code: str | None = None,
) -> AgentState:
    blocks = [CodeBlock(content=code, language="python")] if code else []
    return AgentState(
        input=RawInput(text=question),
        structured_input=StructuredInput(source="text", question=question, code=blocks),
        intent=intent,
        active_problem=active,
        session_progress=progress,
        pending_check=pending,
    )


def _decision(state: AgentState) -> TurnDecision:
    decision = _decide(state).get("decision")
    assert decision is not None
    return decision


def test_an_unsure_reading_keeps_the_open_problem_and_honours_the_code_ask() -> None:
    """The reported turn. "stack" is corpus vocabulary; that used to make the
    message a new subject, and the unsure label then sent it to clarify."""
    update = _decide(_state("give code using stack", intent=_unsure(), active=_active()))

    assert update.get("problem_relation") == "followup"
    assert update.get("route") == "dsa"
    decision = update.get("decision")
    assert decision is not None
    assert decision.subject == "active"
    assert decision.move == "code"
    assert decision.wants_code is True
    assert decision.confident is True  # settled by a rule on the follow-up
    inherited = update.get("structured_input")
    assert inherited is not None
    assert inherited.problem == _P678
    assert inherited.question == "give code using stack"


def test_an_unsure_concept_question_is_still_its_own_question() -> None:
    update = _decide(_state("what is a trie?", intent=_unsure(), active=_active()))

    assert update.get("problem_relation") == "none"
    assert update.get("route") == "explain"
    decision = update.get("decision")
    assert decision is not None
    assert decision.subject == "new"
    assert decision.move == "explain"


def test_a_technique_named_about_the_problem_is_a_follow_up() -> None:
    for text in ("can i solve this with two pointers", "is it a greedy problem?"):
        update = _decide(_state(text, intent=_unsure(), active=_active()))
        assert update.get("problem_relation") == "followup", text
        assert update.get("route") == "dsa", text


async def test_nothing_to_anchor_on_asks_for_what_is_missing() -> None:
    """With no subject in the conversation an unsure turn is asked about, and
    the question says what is missing rather than guessing a label back."""
    state = _state("is their another method for this program?", intent=_unsure())
    update = _decide(state)
    assert update.get("route") == "clarify"
    decision = update.get("decision")
    assert decision is not None
    assert (decision.subject, decision.move, decision.confident) == ("none", "clarify", False)

    reply = await clarify(state.model_copy(update=dict(update)), _Runtime())  # type: ignore[arg-type]
    outcome = reply.get("agent_output")
    assert outcome is not None
    assert "It looks like you might want" not in outcome.text
    assert "no problem or code" in outcome.text


async def test_an_unanchored_turn_names_the_subject_the_conversation_has() -> None:
    state = _state(
        "tell me about your day",
        intent=_sure(Intent.GENERAL_GUIDANCE, refers_to_previous=False),
        active=_active(),
    )
    update = _decide(state)
    assert update.get("route") == "clarify"
    reply = await clarify(state.model_copy(update=dict(update)), _Runtime())  # type: ignore[arg-type]
    outcome = reply.get("agent_output")
    assert outcome is not None
    assert "678. Valid Parenthesis String" in outcome.text


def test_a_follow_up_on_a_plan_is_about_the_last_reply() -> None:
    progress = SessionProgress.empty().model_copy(update={"last_thread": "plan"})
    decision = _decision(
        _state(
            "first where should i start",
            intent=_sure(Intent.DSA_HINT, refers_to_previous=True),
            active=_active(),
            progress=progress,
        )
    )
    assert decision.subject == "last_reply"
    assert decision.move == "plan"


def test_a_greeting_is_greeted() -> None:
    greeting = IntentResult(intent=Intent.GENERAL_GUIDANCE, confidence=0.3, source="rule")
    decision = _decision(_state("hi agent", intent=greeting, active=_active()))
    assert decision.move == "greet"


def test_shared_code_is_debugged_and_a_fix_ask_is_a_code_move() -> None:
    code = "def f(nums):\n    return nums[len(nums)]\n"
    looked = _decision(_state("find the bug", intent=_sure(Intent.CODE_DEBUG), code=code))
    assert (looked.subject, looked.move, looked.wants_code) == ("new", "debug", False)
    fix = _decision(
        _state("fix this code", intent=_sure(Intent.CODE_DEBUG, asks_for_code=True), code=code)
    )
    assert (fix.move, fix.wants_code) == ("code", True)


def test_what_the_learner_showed_is_on_the_decision() -> None:
    stuck = _decision(_state("I don't know how", intent=_sure(Intent.DSA_HINT), active=_active()))
    assert (stuck.evidence, stuck.scaffold) == ("stuck", "up")

    right = _decision(
        _state(
            "the best one-sided path?",
            intent=_sure(Intent.DSA_HINT, refers_to_previous=True, learner_showed="correct"),
            active=_active(),
        )
    )
    assert (right.evidence, right.scaffold) == ("correct", "down")

    # A request is not a response, whatever label came with it.
    asks = _decision(
        _state(
            "give me the code",
            intent=_sure(Intent.DSA_SOLVE, asks_for_code=True, learner_showed="stuck"),
            active=_active(),
        )
    )
    assert asks.evidence == "none"


def test_a_grade_becomes_the_decisions_evidence_label() -> None:
    def grade(value: str, method: str = "accepted", wrong: str | None = None) -> AnswerGrade:
        return AnswerGrade.model_validate(
            {"grade": value, "method": method, "misconception_id": wrong}
        )

    assert _graded_evidence(grade("correct")) == "correct"
    assert _graded_evidence(grade("correct", "llm_terminology")) == "terminology_error"
    assert _graded_evidence(grade("partial")) == "partially_correct"
    assert _graded_evidence(grade("dont_know", "dont_know")) == "stuck"
    assert _graded_evidence(grade("incorrect")) == "incorrect"
    assert (
        _graded_evidence(grade("incorrect", "option", "trees.returnable_vs_global_path"))
        == "conceptual_misconception"
    )


def test_scaffolding_follows_the_evidence() -> None:
    assert scaffold_for("stuck") == "up"
    assert scaffold_for("conceptual_misconception") == "up"
    assert scaffold_for("partially_correct") == "same"
    assert scaffold_for("terminology_error") == "down"
    assert scaffold_for("none") == "same"


def test_a_pending_question_makes_a_reply_a_graded_answer() -> None:
    pending = PendingCheck(
        kind="question",
        question_id="two_sum.need",
        question="What number do you need with 2 to make 9?",
        topic="hashing",
        created_at=datetime.now(UTC),
    )
    decision = _decision(
        _state("7?", intent=_unsure(Intent.GENERAL_GUIDANCE), active=_active(), pending=pending)
    )
    assert decision.move == "grade"


_SHARED = "def maxDepth(root):\n    if root is None:\n        return 0\n    return 0\n"


def _code_subject() -> ActiveProblem:
    return ActiveProblem(
        problem=StructuredInput(
            source="text", code=[CodeBlock(content=_SHARED, language="python")]
        ),
        key="_c1",
        topic="trees",
    )


def test_an_agent_must_have_its_subject() -> None:
    """Measured live: after a debugging reply the learner answered "Add 1.".
    It was read as a new request to solve something, with nothing to solve,
    and the tutor wrote and "verified" a function that adds one."""
    misread = _sure(Intent.DSA_SOLVE, refers_to_previous=False, asks_for_code=True)
    update = _decide(_state("Add 1.", intent=misread, active=_code_subject()))

    assert update.get("problem_relation") == "followup"
    assert update.get("route") == "debug"  # the subject is their code
    inherited = update.get("structured_input")
    assert inherited is not None
    assert inherited.code and inherited.code[0].content == _SHARED
    assert inherited.question == "Add 1."


def test_with_no_subject_at_all_the_turn_is_asked_about() -> None:
    misread = _sure(Intent.DSA_HINT, refers_to_previous=False)
    update = _decide(_state("give me the next hint", intent=misread))
    assert update.get("problem_relation") == "none"


def test_a_concept_question_is_left_alone_by_the_subject_rule() -> None:
    update = _decide(
        _state(
            "what is a trie?",
            intent=_sure(Intent.CONCEPT_EXPLANATION, refers_to_previous=False),
            active=_active(),
        )
    )
    assert update.get("route") == "explain"
    assert update.get("problem_relation") == "none"


def test_a_response_is_not_a_demand_for_the_code() -> None:
    both = _sure(
        Intent.DSA_HINT, refers_to_previous=True, asks_for_code=True, learner_showed="correct"
    )
    decision = _decision(_state("add one to it", intent=both, active=_active()))
    assert decision.wants_code is False
    assert decision.move == "step"
    assert decision.evidence == "correct"
    # A listed phrase in the message itself is still a demand.
    asked = _decision(_state("give me the code", intent=both, active=_active()))
    assert asked.wants_code is True


def test_a_reply_about_shared_code_goes_to_the_debugger() -> None:
    reply = _sure(Intent.CODE_EXPLAIN, refers_to_previous=True, learner_showed="correct")
    update = _decide(_state("return 1 + max(left, right)", intent=reply, active=_code_subject()))
    assert update.get("route") == "debug"


def test_dont_give_me_the_code_yet_is_respected_until_they_ask() -> None:
    said = _sure(Intent.CODE_DEBUG, no_solution=True)
    first = _decision(_state("explain it but no final code yet", intent=said, code=_SHARED))
    assert first.withhold is True

    held = SessionProgress.empty().model_copy(update={"withhold_key": "_c1"})
    later = _decision(
        _state(
            "is it the base case?",
            intent=_sure(Intent.CODE_DEBUG, refers_to_previous=True),
            active=_code_subject(),
            progress=held,
        )
    )
    assert later.withhold is True

    asks = _decision(
        _state(
            "ok give me the code",
            intent=_sure(Intent.CODE_DEBUG, refers_to_previous=True, asks_for_code=True),
            active=_code_subject(),
            progress=held,
        )
    )
    assert (asks.withhold, asks.wants_code) == (False, True)


def test_their_own_no_code_yet_outranks_a_models_reading_of_a_short_reply() -> None:
    """Measured live: after "don't give me the final code yet", the learner's
    answer "Add 1." was read as a demand for the fix and the code was shown."""
    held = SessionProgress.empty().model_copy(update={"withhold_key": "_c1"})
    misread = _sure(Intent.CODE_DEBUG, refers_to_previous=True, asks_for_code=True)
    decision = _decision(_state("Add 1.", intent=misread, active=_code_subject(), progress=held))
    assert (decision.wants_code, decision.withhold) == (False, True)
    assert decision.move == "debug"
